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
from agents.referral import referral_campaign_start, referral_on_connection_accepted

ALL_FUNCTIONS = [
    inngest_queue_processor,
    inngest_budget_reset,
    connection_agent_run,
    connection_acceptance_poller,
    content_agent_reposts,
    content_agent_reactions,
    job_hunter_run,
    referral_campaign_start,
    referral_on_connection_accepted,
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
        elif action_id == "trigger_referral":
            await handle_referral_command(value)
        elif action_id == "repost_now":
            from core.action_queue import ActionQueue
            await ActionQueue.push(
                "content", "repost", {"post_url": value},
                priority=3, is_dry_run=config.DRY_RUN,
            )

    return Response(status_code=200)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "dry_run": config.DRY_RUN, "warmup_week": config.WARMUP_WEEK}
