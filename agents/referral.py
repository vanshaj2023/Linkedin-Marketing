import datetime
import uuid
import inngest
from inngest_client import inngest_client
from db import db
from core.action_queue import ActionQueue
from core.circuit_breaker import CircuitBreaker
from scrapers.people import search_company_employees
from llm.service import score_connection_profile, generate_connection_note
from mailer.email import send_referral_email
from slack.bot import send_referral_alert, send_alert
from config import config


@inngest_client.create_function(
    fn_id="referral-campaign-start",
    trigger=inngest.TriggerEvent(event="referral/campaign.start"),
    retries=1,
)
async def referral_campaign_start(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Discover employees, score, split into 3 batches over 5-7 days."""
    company = ctx.event.data.get("company", "")
    target_role = ctx.event.data.get("target_role", "Software Engineer")
    job_post_url = ctx.event.data.get("job_post_url", "")
    campaign_id = str(uuid.uuid4())[:8]

    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    raw = await step.run("discover", search_company_employees, company, 50)

    scored = []
    for p in raw:
        score = score_connection_profile(p["headline"], p["company"], p.get("mutual_connections", 0))
        scored.append({**p, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:45]

    campaign_doc = {
        "campaign_id": campaign_id,
        "company": company,
        "target_role": target_role,
        "job_post_url": job_post_url,
        "status": "active",
        "created_at": datetime.datetime.utcnow(),
        "targets": [
            {**t, "batch": (i // 15) + 1, "connection_status": "pending",
             "posts_liked": 0, "referral_email_sent": False,
             "referral_email_sent_at": None, "response_received": False, "notes": None}
            for i, t in enumerate(top)
        ],
    }
    await db.referral_campaigns.insert_one(campaign_doc)

    batch1 = top[:15]
    slack_candidates = [
        {**t, "headline": t.get("headline", ""),
         "connection_note": generate_connection_note(t["headline"], t.get("company", ""), "A")}
        for t in batch1
    ]
    await send_referral_alert(company, slack_candidates)

    await step.run("batch-1", _queue_batch, batch1, 1, campaign_id, company)
    await step.sleep("wait-batch-2", datetime.timedelta(days=2))
    await step.run("batch-2", _queue_batch, top[15:30], 2, campaign_id, company)
    await step.sleep("wait-batch-3", datetime.timedelta(days=2))
    await step.run("batch-3", _queue_batch, top[30:45], 3, campaign_id, company)

    return {"status": "done", "campaign_id": campaign_id, "targets": len(top), "dry_run": config.DRY_RUN}


async def _queue_batch(targets: list, batch_num: int, campaign_id: str, company: str) -> dict:
    queued = 0
    for t in targets:
        note = generate_connection_note(t["headline"], t.get("company", ""), "A")

        await ActionQueue.push(
            "referral", "view_profile",
            {"target_profile_url": t["linkedin_url"]},
            priority=1, is_dry_run=config.DRY_RUN,
        )
        await ActionQueue.push(
            "referral", "connect",
            {"target_profile_url": t["linkedin_url"], "message": note},
            priority=1, is_dry_run=config.DRY_RUN,
        )

        await db.engage_list.update_one(
            {"linkedin_url": t["linkedin_url"]},
            {"$setOnInsert": {
                "linkedin_url": t["linkedin_url"],
                "name": t["name"],
                "reason": "referral_target",
                "last_post_url": None,
                "last_engaged_at": None,
                "engagement_count": 0,
                "auto_comment": True,
                "added_by_agent": f"referral:{campaign_id}",
            }},
            upsert=True,
        )

        await db.referral_campaigns.update_one(
            {"campaign_id": campaign_id, "targets.linkedin_url": t["linkedin_url"]},
            {"$set": {"targets.$.connection_status": "sent"}},
        )
        queued += 1

    return {"batch": batch_num, "queued": queued}


@inngest_client.create_function(
    fn_id="referral-on-connection-accepted",
    trigger=inngest.TriggerEvent(event="connection/accepted"),
    retries=1,
)
async def referral_on_connection_accepted(ctx: inngest.Context, step: inngest.Step) -> dict:
    """On connection accepted: wait 3 days, then send referral email."""
    linkedin_url = ctx.event.data.get("linkedin_url", "")
    name = ctx.event.data.get("name", "")

    campaign = await db.referral_campaigns.find_one(
        {"status": "active", "targets.linkedin_url": linkedin_url}
    )
    if not campaign:
        return {"status": "not_in_campaign"}

    target_role = campaign.get("target_role", "Software Engineer")
    company = campaign.get("company", "")

    await step.sleep("wait-3d", datetime.timedelta(days=3))

    email_sent = await step.run(
        "send-email", send_referral_email, "", name, company, target_role,
    )

    if email_sent:
        await db.referral_campaigns.update_one(
            {"_id": campaign["_id"], "targets.linkedin_url": linkedin_url},
            {"$set": {
                "targets.$.connection_status": "accepted",
                "targets.$.referral_email_sent": True,
                "targets.$.referral_email_sent_at": datetime.datetime.utcnow(),
            }},
        )
        await send_alert(f"Referral email queued for {name} @ {company} ({target_role}). DRY_RUN={config.DRY_RUN}")

    return {"status": "done", "email_sent": email_sent, "name": name}
