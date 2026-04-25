import datetime
import inngest
from inngest_client import inngest_client
from db import db
from core.action_queue import ActionQueue
from core.circuit_breaker import CircuitBreaker
from scrapers.feed import scrape_hiring_posts
from llm.service import score_post_for_repost, generate_engage_comment
from slack.bot import send_repost_digest, send_alert
from config import config


@inngest_client.create_function(
    fn_id="content-agent-reposts",
    trigger=inngest.TriggerCron(cron="0 8,17 * * *"),
    retries=2,
)
async def content_agent_reposts(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Twice daily: scan feed -> score posts -> Slack digest + auto-repost."""
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    scored_posts = []
    for keyword in config.TARGET_KEYWORDS[:2]:
        posts = await step.run(
            f"scrape-{keyword.replace(' ', '_')}",
            scrape_hiring_posts, keyword, 5,
        )
        for post in posts:
            data = score_post_for_repost(
                author_name=post["author_name"],
                content=post["content"],
                likes=0, comments=0, hours_old=1.0,
            )
            if data.get("score", 0) >= 60:
                scored_posts.append({
                    "post_url": post["post_url"],
                    "author_name": post["author_name"],
                    "content": post["content"],
                    "score": data["score"],
                    "reasoning": data.get("reasoning", ""),
                    "suggested_caption": data.get("suggested_caption", ""),
                })

    scored_posts.sort(key=lambda x: x["score"], reverse=True)
    top = scored_posts[:5]

    auto_reposted = []
    manual = []
    for p in top:
        if p["score"] >= config.AUTO_REPOST_SCORE_THRESHOLD:
            await ActionQueue.push(
                "content", "repost", {"post_url": p["post_url"]},
                priority=4, is_dry_run=config.DRY_RUN,
            )
            auto_reposted.append(p["post_url"])
        else:
            manual.append(p)

    if manual:
        await step.run("slack-digest", send_repost_digest, manual)
    if auto_reposted:
        await send_alert(f"Auto-reposted {len(auto_reposted)} posts. DRY_RUN={config.DRY_RUN}")

    return {"status": "done", "auto_reposted": len(auto_reposted), "manual": len(manual)}


@inngest_client.create_function(
    fn_id="content-agent-reactions",
    trigger=inngest.TriggerCron(cron="0 10,14,20 * * *"),
    retries=2,
)
async def content_agent_reactions(ctx: inngest.Context, step: inngest.Step) -> dict:
    """3x daily: like/comment on engage list members' posts."""
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    engage_list = await db.engage_list.find({}).to_list(length=100)
    queued_likes = 0
    queued_comments = 0
    now = datetime.datetime.utcnow()

    for member in engage_list:
        last_engaged = member.get("last_engaged_at")
        hours_since = (now - last_engaged).total_seconds() / 3600 if last_engaged else 999
        last_post_url = member.get("last_post_url")
        if not last_post_url:
            continue

        if hours_since >= 24:
            await ActionQueue.push(
                "content", "like", {"post_url": last_post_url},
                priority=4, is_dry_run=config.DRY_RUN,
            )
            queued_likes += 1

            if member.get("auto_comment", False) and hours_since >= 72:
                comment = generate_engage_comment(
                    member["name"], member.get("last_post_content", "their recent post"),
                )
                await ActionQueue.push(
                    "content", "comment",
                    {"post_url": last_post_url, "message": comment},
                    priority=4, is_dry_run=config.DRY_RUN,
                )
                queued_comments += 1

            await db.engage_list.update_one(
                {"linkedin_url": member["linkedin_url"]},
                {"$set": {"last_engaged_at": now}},
            )

    return {"status": "done", "likes": queued_likes, "comments": queued_comments}
