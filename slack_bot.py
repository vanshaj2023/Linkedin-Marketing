import os
import asyncio
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from system_health import CircuitBreaker, BudgetManager
from database import db

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ALERTS = "#system-health"
SLACK_CHANNEL_JOBS = "#job-alerts"
SLACK_CHANNEL_CONTENT = "#repost-suggestions"
SLACK_CHANNEL_REFERRALS = "#referral-campaigns"

_slack_client = AsyncWebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

async def send_alert(message: str, channel: str = SLACK_CHANNEL_ALERTS):
    """Send an alert to the system-health channel."""
    if not _slack_client:
        print(f"[SLACK SKIPPED] {channel}: {message}")
        return
        
    try:
        await _slack_client.chat_postMessage(channel=channel, text=message)
    except SlackApiError as e:
        print(f"Error sending message to Slack: {e.response['error']}")

async def send_repost_digest(posts: list):
    """Sends a formatted digest of highly scored posts to the repost-suggestions channel."""
    if not posts: return
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📢 Content Agent: Repost Suggestions (Morning Digest)",
                "emoji": True
            }
        }
    ]
    
    for idx, p in enumerate(posts, 1):
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{idx}. [Score: {p['score']}] {p['author_name']}*\n_{p['content'][:150]}..._\n\n*Why it matched:* {p['reasoning']}\n*Caption Idea:* {p['suggested_caption']}\n*Link:* <{p['post_url']}|View Post>"
            }
        })
        blocks.append({"type": "divider"})
        
    if not _slack_client:
        print("[SLACK SKIPPED] Repost Digest Dump:")
        print(blocks)
        return
        
    try:
        await _slack_client.chat_postMessage(channel=SLACK_CHANNEL_CONTENT, blocks=blocks, text="New Repost Suggestions")
    except SlackApiError as e:
        print(f"Error sending digest to Slack: {e.response['error']}")

async def handle_status_command():
    """Generates the response for /status."""
    health = await CircuitBreaker.status()
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    budgets = await db.daily_budgets.find_one({"date": today_str})
    
    queued_count = await db.action_queue.count_documents({"status": "queued"})
    
    msg = f"*System Status:* {health.upper()}\n"
    msg += f"*Queue Backlog:* {queued_count} actions pending\n"
    msg += "*Daily Budgets:* \n"
    if budgets:
        for k, v in budgets.items():
            if isinstance(v, dict) and "used" in v and "limit" in v:
                msg += f"  - {k}: {v['used']}/{v['limit']}\n"
    return msg

async def handle_pause_command():
    """Handles the /pause command logic to trip the circuit breaker."""
    await CircuitBreaker.trip("red", "Manually paused via Slack /pause", auto_resume_hours=None)
    await send_alert("System has been manually PAUSED via Slack.", SLACK_CHANNEL_ALERTS)
    return "System paused successfully."

async def handle_resume_command():
    """Handles the /resume command logic."""
    await CircuitBreaker.reset()
    await send_alert("System has been manually RESUMED via Slack.", SLACK_CHANNEL_ALERTS)
    return "System resumed successfully."
