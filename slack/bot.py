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
CHANNEL_FEED_JOBS = "#feed-job-alerts"
CHANNEL_APPLY = "#auto-apply"

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
    poster_url = job.get("poster_url") or ""
    poster_name = job.get("poster_name") or ""
    poster_line = f"*Posted by:* {poster_name}\n" if poster_name else ""
    action_buttons = [
        {"type": "button", "text": {"type": "plain_text", "text": "Mark Applied"},
         "action_id": "mark_applied", "value": job["linkedin_post_url"], "style": "primary"},
        {"type": "button", "text": {"type": "plain_text", "text": "Trigger Referral"},
         "action_id": "trigger_referral", "value": job["company"]},
        {"type": "button", "text": {"type": "plain_text", "text": "Dismiss"},
         "action_id": "dismiss_job", "value": job["linkedin_post_url"], "style": "danger"},
    ]
    if poster_url:
        action_buttons.insert(2, {
            "type": "button",
            "text": {"type": "plain_text", "text": "Connect to Poster"},
            "action_id": "connect_poster",
            "value": f"{poster_url}::{poster_name} at {job['company']}",
        })
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Job Match ({job.get('relevance_score', 0)}/100): {job['job_title']} @ {job['company']}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{poster_line}*Why:*\n{job.get('reasoning', 'N/A')}\n\n<{job['linkedin_post_url']}|View on LinkedIn>"}},
        {"type": "actions", "elements": action_buttons},
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


async def send_feed_job_alert(post: dict) -> str | None:
    cls = post.get("classification", {})
    role = cls.get("role") or "Job opportunity"
    company = cls.get("company") or "Unknown company"
    score = cls.get("relevance_score", 0)
    method = cls.get("apply_method", "unknown")
    target = cls.get("apply_target") or ""
    reasoning = cls.get("reasoning", "")
    author = post.get("author_name", "Unknown")
    author_url = post.get("author_url") or ""
    post_url = post.get("post_url") or ""
    snippet = (post.get("content") or "")[:200]

    apply_hint = f"Apply via: {method}" + (f" — {target}" if target else "")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Feed Job ({score}/100): {role} @ {company}", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Posted by:* {author}\n"
                    f"*Why:* {reasoning}\n"
                    f"*How to apply:* {apply_hint}\n\n"
                    f"_{snippet}{'...' if len(post.get('content','')) > 200 else ''}_"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                *(
                    [{"type": "button", "text": {"type": "plain_text", "text": "Connect with Poster"},
                      "action_id": "connect_poster",
                      "value": f"{author_url}::{role} at {company}",
                      "style": "primary"}]
                    if author_url else []
                ),
                *(
                    [{"type": "button", "text": {"type": "plain_text", "text": "Open Post"},
                      "action_id": "open_feed_post", "value": post_url}]
                    if post_url else []
                ),
                {"type": "button", "text": {"type": "plain_text", "text": "Dismiss"},
                 "action_id": "dismiss_feed_job", "value": post.get("post_id", ""),
                 "style": "danger"},
            ],
        },
    ]

    if not _client:
        print(f"[SLACK SKIPPED] Feed job alert: {role} @ {company} (score={score})")
        return None
    try:
        resp = await _client.chat_postMessage(
            channel=CHANNEL_FEED_JOBS, blocks=blocks, text=f"Feed job: {role} @ {company}"
        )
        return resp.get("ts")
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")
        return None


async def send_manual_apply_alert(job: dict, score_data: dict) -> str | None:
    title = job.get("job_title", "Unknown Role")
    company = job.get("company", "Unknown")
    score = score_data.get("relevance_score", 0)
    reasoning = score_data.get("reasoning", "")
    apply_url = job.get("external_apply_url") or job.get("linkedin_post_url", "")
    posted = job.get("posted_ago_text", "")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Apply Now ({score}/100): {title} @ {company}", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Why:* {reasoning}\n"
                    f"*Posted:* {posted}\n"
                    f"*Apply:* <{apply_url}|Open application>"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Open ATS"},
                 "action_id": "open_ats", "value": apply_url, "style": "primary"},
                {"type": "button", "text": {"type": "plain_text", "text": "Mark Applied"},
                 "action_id": "mark_applied", "value": job.get("linkedin_post_url", "")},
                {"type": "button", "text": {"type": "plain_text", "text": "Trigger Referral"},
                 "action_id": "trigger_referral", "value": company},
            ],
        },
    ]

    if not _client:
        print(f"[SLACK SKIPPED] Manual apply alert: {title} @ {company}")
        return None
    try:
        resp = await _client.chat_postMessage(
            channel=CHANNEL_APPLY, blocks=blocks, text=f"Apply: {title} @ {company}"
        )
        return resp.get("ts")
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")
        return None


async def send_apply_success(job: dict) -> None:
    title = job.get("job_title", "Unknown Role")
    company = job.get("company", "Unknown")
    url = job.get("linkedin_post_url", "")
    if not _client:
        print(f"[SLACK SKIPPED] Easy Apply success: {title} @ {company}")
        return
    try:
        await _client.chat_postMessage(
            channel=CHANNEL_APPLY,
            text=f"Applied via Easy Apply: *{title}* @ *{company}*\n<{url}|View job>",
        )
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")


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


async def send_referral_approval(
    target: dict, company: str, days_since_request: float, proposed_msg: str,
) -> str | None:
    """Ask the user whether to send a referral DM that exceeded the 2-day
    auto-send window. Returns the Slack message timestamp."""
    name = target.get("name", "Unknown")
    headline = target.get("headline", "")
    linkedin_url = target.get("linkedin_url", "")
    tier = target.get("tier", "ic")

    blocks = [
        {"type": "header", "text": {"type": "plain_text",
            "text": f"Referral ask: {name} @ {company}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*{name}* ({tier}) accepted *{days_since_request:.1f} days* "
            f"after the connect request (>2d window).\n"
            f"_{headline}_\n"
            f"<{linkedin_url}|View profile>\n\n"
            f"*Proposed DM:*\n>>> {proposed_msg}"
        )}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Send DM"},
             "action_id": "approve_referral_dm", "value": linkedin_url,
             "style": "primary"},
            {"type": "button", "text": {"type": "plain_text", "text": "Skip"},
             "action_id": "skip_referral_dm", "value": linkedin_url,
             "style": "danger"},
        ]},
    ]

    if not _client:
        print(f"[SLACK SKIPPED] Referral approval: {name} @ {company}")
        return None
    try:
        resp = await _client.chat_postMessage(
            channel=CHANNEL_REFERRALS, blocks=blocks,
            text=f"Approve referral DM to {name} @ {company}?",
        )
        return resp.get("ts")
    except SlackApiError as e:
        print(f"Slack error: {e.response['error']}")
        return None


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
