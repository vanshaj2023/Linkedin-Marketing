import datetime
import uuid
import inngest
from inngest_client import inngest_client
from db import db
from core.action_queue import ActionQueue
from core.circuit_breaker import CircuitBreaker
from scrapers.people import search_people
from llm.service import generate_connection_note, generate_referral_message
from slack.bot import send_referral_alert, send_referral_approval
from agents.connection import _check_if_connected
from config import config

AUTO_SEND_WINDOW_DAYS = 2
APPROVAL_TIMEOUT_DAYS = 3

# Priority tiers: lower number = sent to LinkedIn first.
# Each tier maps a label to the role keywords that go into people-search.
TIERS = [
    (1, "hr", [
        "HR", "Recruiter", "Talent Acquisition", "Technical Recruiter",
        "Talent Partner", "People Operations",
    ]),
    (2, "hiring_manager", [
        "Engineering Manager", "Director of Engineering",
        "Head of Engineering", "VP Engineering",
    ]),
    (3, "ic", [
        "Software Engineer", "Senior Software Engineer",
        "Staff Engineer", "Backend Engineer",
    ]),
]

MAX_PER_KEYWORD = 8
MAX_TOTAL_PER_CAMPAIGN = 30


@inngest_client.create_function(
    fn_id="referral-campaign-start",
    trigger=inngest.TriggerEvent(event="referral/campaign.start"),
    retries=1,
)
async def referral_campaign_start(ctx: inngest.Context, step: inngest.Step) -> dict:
    """One-shot referral flow:
    company name in -> search company employees by role tier -> queue connect
    requests. HR / recruiters go out first, then hiring managers, then ICs.
    """
    company = (ctx.event.data.get("company") or "").strip()
    if not company:
        return {"status": "skipped", "reason": "no_company"}

    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    campaign_id = str(uuid.uuid4())[:8]
    job_post_url = ctx.event.data.get("job_post_url", "")
    target_role = ctx.event.data.get("target_role", "Software Engineer")

    # ── Discover candidates, tier by tier ────────────────────────────────────
    seen_urls: set[str] = set()
    candidates: list[dict] = []

    for priority, tier_label, keywords in TIERS:
        for kw in keywords:
            if len(candidates) >= MAX_TOTAL_PER_CAMPAIGN:
                break

            results = await step.run(
                f"search-{tier_label}-{kw.replace(' ', '_')}",
                search_people, f"{kw} {company}", MAX_PER_KEYWORD,
            )

            for p in results:
                url = p.get("linkedin_url", "")
                if not url or url in seen_urls:
                    continue
                # Cheap company-match filter: company name appears anywhere in
                # the company / headline (LinkedIn people-search returns roles
                # at OTHER companies too).
                blob = f"{p.get('company', '')} {p.get('headline', '')}".lower()
                if company.lower() not in blob:
                    continue
                seen_urls.add(url)
                candidates.append({
                    **p,
                    "priority": priority,
                    "tier": tier_label,
                })
                if len(candidates) >= MAX_TOTAL_PER_CAMPAIGN:
                    break

    if not candidates:
        return {"status": "done", "campaign_id": campaign_id, "queued": 0,
                "reason": "no_candidates"}

    # ── Persist campaign ─────────────────────────────────────────────────────
    await db.referral_campaigns.insert_one({
        "campaign_id": campaign_id,
        "company": company,
        "target_role": target_role,
        "job_post_url": job_post_url,
        "status": "active",
        "created_at": datetime.datetime.utcnow(),
        "targets": [
            {
                "linkedin_url": c["linkedin_url"],
                "name": c["name"],
                "headline": c.get("headline", ""),
                "tier": c["tier"],
                "priority": c["priority"],
                "connection_status": "pending",
            }
            for c in candidates
        ],
    })

    # ── Queue connect requests (priority preserved via ActionQueue) ──────────
    queued = await step.run(
        "queue-connects", _queue_connects, candidates, campaign_id, company,
    )

    # ── Slack heads-up: top 10 to make decisions visible ─────────────────────
    preview = [
        {
            "name": c["name"],
            "headline": c.get("headline", ""),
            "linkedin_url": c["linkedin_url"],
            "connection_note": generate_connection_note(
                c.get("headline", ""), c.get("company", company), "A",
            ),
        }
        for c in candidates[:10]
    ]
    await step.run("slack-alert", send_referral_alert, company, preview)

    return {
        "status": "done",
        "campaign_id": campaign_id,
        "queued": queued,
        "company": company,
        "by_tier": {
            label: sum(1 for c in candidates if c["tier"] == label)
            for _, label, _ in TIERS
        },
        "dry_run": config.DRY_RUN,
    }


async def _queue_connects(
    candidates: list, campaign_id: str, company: str,
) -> int:
    target_role = ""
    campaign = await db.referral_campaigns.find_one({"campaign_id": campaign_id})
    if campaign:
        target_role = campaign.get("target_role", "")

    queued = 0
    for c in candidates:
        note = generate_connection_note(
            c.get("headline", ""), c.get("company", company), "A",
        )
        # Pre-compute the referral DM now so:
        #  1. Slack approval shows the EXACT message that'll be sent.
        #  2. LLM cost is paid once per target, not on every poller pass.
        proposed = generate_referral_message(
            to_name=c["name"], to_headline=c.get("headline", ""),
            company=company, target_role=target_role,
        )
        await ActionQueue.push(
            "referral", "view_profile",
            {"target_profile_url": c["linkedin_url"]},
            priority=c["priority"], is_dry_run=config.DRY_RUN,
        )
        await ActionQueue.push(
            "referral", "connect",
            {"target_profile_url": c["linkedin_url"], "message": note},
            priority=c["priority"], is_dry_run=config.DRY_RUN,
        )
        await db.referral_campaigns.update_one(
            {"campaign_id": campaign_id, "targets.linkedin_url": c["linkedin_url"]},
            {"$set": {
                "targets.$.connection_status": "queued",
                "targets.$.proposed_referral_msg": proposed,
            }},
        )
        queued += 1
    return queued


# ── Acceptance poller ────────────────────────────────────────────────────────

@inngest_client.create_function(
    fn_id="referral-acceptance-poller",
    trigger=inngest.TriggerCron(cron="0 */4 * * *"),
    retries=1,
)
async def referral_acceptance_poller(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Every 4h: walk active referral targets, decide what to do.

      - Not-yet-accepted + connect was sent → check if they accepted.
        - Accepted within AUTO_SEND_WINDOW_DAYS → queue DM automatically.
        - Accepted later → post Slack approval, mark awaiting_approval.
      - Awaiting approval for > APPROVAL_TIMEOUT_DAYS → auto-skip.
    """
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    now = datetime.datetime.utcnow()
    checked = auto_sent = awaiting = timed_out = 0

    # 1. Time out stale awaiting_approval rows
    timeout_cutoff = now - datetime.timedelta(days=APPROVAL_TIMEOUT_DAYS)
    timeout_res = await db.referral_campaigns.update_many(
        {"targets": {"$elemMatch": {
            "referral_msg_status": "awaiting_approval",
            "referral_msg_decided_at": {"$lt": timeout_cutoff},
        }}},
        {"$set": {
            "targets.$[t].referral_msg_status": "skipped",
        }},
        array_filters=[{
            "t.referral_msg_status": "awaiting_approval",
            "t.referral_msg_decided_at": {"$lt": timeout_cutoff},
        }],
    )
    timed_out = timeout_res.modified_count

    # 2. Walk active campaigns, look at queued+sent-but-undecided targets
    campaigns = await db.referral_campaigns.find({"status": "active"}).to_list(length=200)
    for camp in campaigns:
        company = camp.get("company", "")
        for t in camp.get("targets", []):
            if t.get("referral_msg_status") != "pending":
                continue
            if t.get("connection_status") not in ("queued", "accepted"):
                continue
            sent_at = t.get("request_sent_at")
            if not sent_at:
                # Connect action hasn't run yet (still in the queue).
                continue

            url = t["linkedin_url"]
            checked += 1

            # Re-use the existing acceptance check helper.
            is_accepted = await step.run(
                f"check-{camp['campaign_id']}-{url[-30:]}",
                _check_if_connected, url,
            )
            if not is_accepted:
                continue

            days_since = (now - sent_at).total_seconds() / 86400.0
            proposed_msg = t.get("proposed_referral_msg") or generate_referral_message(
                to_name=t["name"], to_headline=t.get("headline", ""),
                company=company, target_role=camp.get("target_role", ""),
            )

            if days_since <= AUTO_SEND_WINDOW_DAYS:
                # Auto-send: queue a DM. Action-queue dispatcher flips
                # referral_msg_status to "auto_sent" on success.
                await ActionQueue.push(
                    "referral", "dm",
                    {
                        "target_profile_url": url,
                        "message": proposed_msg,
                        "auto": True,
                    },
                    priority=t.get("priority", 3),
                    is_dry_run=config.DRY_RUN,
                )
                await db.referral_campaigns.update_one(
                    {"campaign_id": camp["campaign_id"], "targets.linkedin_url": url},
                    {"$set": {
                        "targets.$.connection_status": "accepted",
                        "targets.$.accepted_at": now,
                        "targets.$.referral_msg_decided_at": now,
                    }},
                )
                auto_sent += 1
            else:
                # Out of window — ask in Slack.
                ts = await send_referral_approval(
                    target={
                        "name": t["name"],
                        "headline": t.get("headline", ""),
                        "linkedin_url": url,
                        "tier": t.get("tier", "ic"),
                    },
                    company=company,
                    days_since_request=days_since,
                    proposed_msg=proposed_msg,
                )
                await db.referral_campaigns.update_one(
                    {"campaign_id": camp["campaign_id"], "targets.linkedin_url": url},
                    {"$set": {
                        "targets.$.connection_status": "accepted",
                        "targets.$.accepted_at": now,
                        "targets.$.referral_msg_status": "awaiting_approval",
                        "targets.$.referral_msg_decided_at": now,
                        "targets.$.slack_approval_ts": ts,
                        "targets.$.proposed_referral_msg": proposed_msg,
                    }},
                )
                awaiting += 1

    return {
        "status": "done",
        "checked": checked,
        "auto_sent": auto_sent,
        "awaiting_approval": awaiting,
        "timed_out": timed_out,
    }
