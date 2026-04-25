import os
import datetime
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from core.circuit_breaker import CircuitBreaker
from db import db

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
CHANNEL_ALERTS = "#system-alerts"
CHANNEL_JOBS = "#job-alerts"
CHANNEL_CONTENT = "#repost-suggestions"
CHANNEL_REFERRALS = "#referral-campaigns"

_client = AsyncWebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None


async def send_alert(message: str, level: str = "info"):
    if not _client:
        print(f"[SLACK {level.upper()}]: {message}")
        return
    prefix = {"error": "\U0001f534 ERROR", "warn": "\U0001f7e1 WARN"}.get(level, "\U0001f7e2 INFO")
    try:
        await _client.chat_postMessage(channel=CHANNEL_ALERTS, text=f"{prefix}: {message}")
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")


async def send_repost_digest(posts: list):
    if not posts:
        return
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Repost Suggestions", "emoji": True}},
    ]
    for idx, p in enumerate(posts, 1):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{idx}. [Score: {p['score']}] {p['author_name']}*\n"
                    f"_{p['content'][:150]}..._\n"
                    f"*Why:* {p['reasoning']}\n"
                    f"*Caption:* {p['suggested_caption']}\n"
                    f"<{p['post_url']}|View Post>"
                ),
            },
        })
        blocks.append({
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Repost Now"}, "action_id": "repost_now", "value": p["post_url"], "style": "primary"},
                {"type": "button", "text": {"type": "plain_text", "text": "Skip"}, "action_id": "skip_repost", "value": p["post_url"]},
            ],
        })
        blocks.append({"type": "divider"})

    if not _client:
        print("[SLACK SKIPPED] Repost digest:", len(posts), "posts")
        return
    try:
        await _client.chat_postMessage(channel=CHANNEL_CONTENT, blocks=blocks, text="New Repost Suggestions")
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")


async def send_job_alert(job: dict) -> str | None:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Job Match ({job.get('relevance_score', 0)}/100): {job['job_title']} @ {job['company']}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why:*\n{job.get('reasoning', 'N/A')}\n\n<{job['linkedin_post_url']}|View on LinkedIn>"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Mark Applied"}, "action_id": "mark_applied", "value": job["linkedin_post_url"], "style": "primary"},
            {"type": "button", "text": {"type": "plain_text", "text": "Trigger Referral"}, "action_id": "trigger_referral", "value": job["company"]},
            {"type": "button", "text": {"type": "plain_text", "text": "Dismiss"}, "action_id": "dismiss_job", "value": job["linkedin_post_url"], "style": "danger"},
        ]},
    ]
    if not _client:
        print("[SLACK SKIPPED] Job alert:", job["job_title"])
        return None
    try:
        resp = await _client.chat_postMessage(channel=CHANNEL_JOBS, blocks=blocks, text="New Job Match!")
        return resp.get("ts")
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")
        return None


async def send_referral_alert(company: str, candidates: list):
    if not candidates:
        return
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Referral Campaign: {company}", "emoji": True}},
    ]
    for idx, c in enumerate(candidates, 1):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{idx}. {c['name']}* ({c.get('headline', '')})\n*Note:* _{c.get('connection_note', '')}_\n<{c['linkedin_url']}|Profile>"},
        })
        blocks.append({"type": "divider"})

    if not _client:
        print("[SLACK SKIPPED] Referral alert:", company)
        return
    try:
        await _client.chat_postMessage(channel=CHANNEL_REFERRALS, blocks=blocks, text=f"Referral campaign: {company}")
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")


# ── Slash command handlers ───────────────────────────────────────────────────

async def handle_status_command() -> str:
    health = await CircuitBreaker.status()
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    budgets = await db.daily_budgets.find_one({"date": today_str})
    queued = await db.action_queue.count_documents({"status": "queued"})

    msg = f"*Status:* {health.upper()}\n*Queue:* {queued} pending\n*Budgets:*\n"
    if budgets:
        for k, v in budgets.items():
            if isinstance(v, dict) and "used" in v:
                msg += f"  - {k}: {v['used']}/{v['limit']}\n"
    return msg


async def handle_pause_command() -> str:
    await CircuitBreaker.trip("red", "Manually paused via /pause")
    await send_alert("System PAUSED via Slack.")
    return "System paused."


async def handle_resume_command() -> str:
    await CircuitBreaker.reset()
    await send_alert("System RESUMED via Slack.")
    return "System resumed."


async def handle_referral_command(company: str) -> str:
    from inngest_client import inngest_client
    import inngest
    await inngest_client.send(
        inngest.Event(name="referral/campaign.start", data={"company": company, "source": "slack_command"})
    )
    return f"Referral campaign triggered for *{company}*."
