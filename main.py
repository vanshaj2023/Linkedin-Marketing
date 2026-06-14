import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager

import inngest.fast_api
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse

from inngest_client import inngest_client
from db import setup_indexes
from core.warmup import apply_warmup_budget
from config import config
from slack.bot import (
    handle_status_command,
    handle_pause_command,
    handle_resume_command,
    handle_referral_command,
)

# ── Import all Inngest functions ─────────────────────────────────────────────
from core.action_queue import inngest_queue_processor, inngest_budget_reset
from agents.connection import connection_agent_run, connection_acceptance_poller
from agents.content import content_agent_reposts, content_agent_reactions
from agents.job_hunter import job_hunter_run
from agents.referral import referral_campaign_start, referral_acceptance_poller
from agents.feed_scout import feed_scout_run
from agents.auto_apply import auto_apply_run

ALL_FUNCTIONS = [
    inngest_queue_processor,
    inngest_budget_reset,
    connection_agent_run,
    connection_acceptance_poller,
    content_agent_reposts,
    content_agent_reactions,
    job_hunter_run,
    referral_campaign_start,
    referral_acceptance_poller,
    feed_scout_run,
    auto_apply_run,
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await setup_indexes()
    await apply_warmup_budget(week=config.WARMUP_WEEK)
    print(f"LinkedIn Automation started. DRY_RUN={config.DRY_RUN}, WARMUP_WEEK={config.WARMUP_WEEK}")
    yield


app = FastAPI(title="LinkedIn Automation", lifespan=lifespan)
inngest.fast_api.serve(app, inngest_client, ALL_FUNCTIONS, serve_path="/api/inngest")


# ── Slack signature verification ─────────────────────────────────────────────

def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    if not config.SLACK_SIGNING_SECRET:
        return True
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.HMAC(
        config.SLACK_SIGNING_SECRET.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Slack Slash Commands ─────────────────────────────────────────────────────

@app.post("/slack/commands")
async def slack_commands(request: Request):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    try:
        if abs(time.time() - float(timestamp)) > 300:
            raise HTTPException(status_code=400, detail="Timestamp too old")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid timestamp")

    if not _verify_slack_signature(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    form = await request.form()
    command = form.get("command", "")
    text = (form.get("text", "") or "").strip()

    if command in ("/status", "/li-status"):
        msg = await handle_status_command()
    elif command == "/pause":
        msg = await handle_pause_command()
    elif command == "/resume":
        msg = await handle_resume_command()
    elif command == "/dryrun":
        import config as cfg_module
        cfg_module.config.DRY_RUN = text.lower() == "on"
        msg = f"Dry-run mode: *{'ON' if cfg_module.config.DRY_RUN else 'OFF'}*"
    elif command == "/referral":
        if not text:
            msg = "Usage: `/referral <Company Name>`"
        else:
            msg = await handle_referral_command(text)
    else:
        msg = f"Unknown command: `{command}`"

    return JSONResponse({"response_type": "in_channel", "text": msg})


# ── Slack Interactive Actions ────────────────────────────────────────────────

@app.post("/slack/actions")
async def slack_actions(request: Request):
    form = await request.form()
    try:
        payload = json.loads(form.get("payload", "{}"))
    except json.JSONDecodeError:
        return Response(status_code=400)

    for action in payload.get("actions", []):
        action_id = action.get("action_id", "")
        value = action.get("value", "")

        if action_id == "mark_applied":
            import datetime as dt
            from db import db
            await db.jobs.update_one(
                {"linkedin_post_url": value},
                {"$set": {"applied": True, "applied_at": dt.datetime.utcnow()}},
            )
            await db.recent_jobs.update_one(
                {"linkedin_post_url": value},
                {"$set": {"status": "applied", "applied_at": dt.datetime.utcnow()}},
            )
        elif action_id == "trigger_referral":
            await handle_referral_command(value)
        elif action_id == "repost_now":
            from core.action_queue import ActionQueue
            await ActionQueue.push(
                "content", "repost", {"post_url": value},
                priority=3, is_dry_run=config.DRY_RUN,
            )
        elif action_id == "connect_poster":
            from core.action_queue import ActionQueue
            from llm.service import generate_connection_note
            parts = value.split("::", 1)
            poster_url = parts[0]
            note_seed = parts[1] if len(parts) > 1 else ""
            if poster_url:
                note = generate_connection_note(
                    headline=note_seed, post_summary=note_seed, template_id="A"
                )
                await ActionQueue.push(
                    "feed_scout", "connect",
                    {"target_profile_url": poster_url, "message": note},
                    priority=2, is_dry_run=config.DRY_RUN,
                )
        elif action_id == "approve_referral_dm":
            from db import db
            import datetime as dt
            from core.action_queue import ActionQueue
            camp = await db.referral_campaigns.find_one(
                {"targets.linkedin_url": value},
                {"company": 1, "targets.$": 1},
            )
            if camp and camp.get("targets"):
                t = camp["targets"][0]
                msg = t.get("proposed_referral_msg") or ""
                if msg:
                    await ActionQueue.push(
                        "referral", "dm",
                        {"target_profile_url": value, "message": msg, "auto": False},
                        priority=t.get("priority", 3),
                        is_dry_run=config.DRY_RUN,
                    )
                    await db.referral_campaigns.update_one(
                        {"targets.linkedin_url": value},
                        {"$set": {
                            "targets.$.referral_msg_status": "approved",
                            "targets.$.referral_msg_decided_at": dt.datetime.utcnow(),
                        }},
                    )
        elif action_id == "skip_referral_dm":
            from db import db
            import datetime as dt
            await db.referral_campaigns.update_one(
                {"targets.linkedin_url": value},
                {"$set": {
                    "targets.$.referral_msg_status": "skipped",
                    "targets.$.referral_msg_decided_at": dt.datetime.utcnow(),
                }},
            )
        elif action_id == "dismiss_feed_job":
            from db import db
            await db.feed_job_posts.update_one(
                {"post_id": value},
                {"$set": {"status": "dismissed"}},
            )

    return Response(status_code=200)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "dry_run": config.DRY_RUN, "warmup_week": config.WARMUP_WEEK}
