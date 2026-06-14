import datetime
import inngest
from inngest_client import inngest_client
from db import db
from core.circuit_breaker import CircuitBreaker
from scrapers.feed import scrape_organic_feed
from llm.service import classify_feed_post_as_job
from slack.bot import send_feed_job_alert
from config import config

RELEVANCE_THRESHOLD = 60


@inngest_client.create_function(
    fn_id="feed-scout-run",
    trigger=inngest.TriggerCron(cron="*/30 8-22 * * *"),
    retries=1,
)
async def feed_scout_run(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Every 30 min during waking hours: scan home feed for hiring/job posts."""
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    posts = await step.run(
        "scrape-feed",
        lambda: scrape_organic_feed(max_posts=40),
    )

    alerted = 0
    skipped = 0

    for post in posts:
        post_id = post.get("post_id")
        if not post_id:
            continue

        existing = await db.feed_job_posts.find_one({"post_id": post_id})
        if existing:
            continue

        content = post.get("content", "")
        if len(content) < 80:
            await db.feed_job_posts.insert_one({
                "post_id": post_id,
                "post_url": post.get("post_url", ""),
                "author_name": post.get("author_name", ""),
                "author_url": post.get("author_url"),
                "content_snippet": content[:300],
                "is_job_post": False,
                "classification": {},
                "slack_ts": None,
                "status": "skipped_short",
                "discovered_at": datetime.datetime.utcnow(),
                "last_action_at": None,
            })
            skipped += 1
            continue

        cls = classify_feed_post_as_job(content, post.get("author_name", ""))

        doc = {
            "post_id": post_id,
            "post_url": post.get("post_url", ""),
            "author_name": post.get("author_name", ""),
            "author_url": post.get("author_url"),
            "content_snippet": content[:300],
            "is_job_post": cls["is_job_post"],
            "classification": cls,
            "slack_ts": None,
            "status": "skipped",
            "discovered_at": datetime.datetime.utcnow(),
            "last_action_at": None,
        }

        if cls["is_job_post"] and cls["relevance_score"] >= RELEVANCE_THRESHOLD:
            ts = await send_feed_job_alert({**post, "classification": cls})
            doc["slack_ts"] = ts
            doc["status"] = "alerted"
            alerted += 1
        else:
            skipped += 1

        await db.feed_job_posts.insert_one(doc)

    return {"status": "done", "alerted": alerted, "skipped": skipped, "total": len(posts)}
