import datetime
import inngest
from inngest_client import inngest_client
from db import db
from core.action_queue import ActionQueue
from core.circuit_breaker import CircuitBreaker
from scrapers.jobs_recent import scrape_recent_jobs
from llm.service import score_job_post
from slack.bot import send_manual_apply_alert
from config import config

EASY_APPLY_MIN_SCORE = 75
MANUAL_APPLY_MIN_SCORE = 65


@inngest_client.create_function(
    fn_id="auto-apply-run",
    trigger=inngest.TriggerCron(cron="0 */2 * * *"),
    retries=1,
)
async def auto_apply_run(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Every 2 hours: scrape recent LinkedIn jobs, auto-apply (Easy Apply) or
    send Slack alert for manual application."""
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    kw = config.TARGET_JOB_KEYWORDS[:3]
    locs = config.TARGET_JOB_LOCATIONS[:2]
    jobs = await step.run(
        "scrape-recent-jobs",
        lambda: scrape_recent_jobs(keywords=kw, locations=locs, max_per_combo=20),
    )

    queued_apply = 0
    slack_manual = 0
    low_score = 0

    for job in jobs:
        url = job.get("linkedin_post_url", "")
        if not url:
            continue

        existing = await db.recent_jobs.find_one({"linkedin_post_url": url})
        if existing:
            continue

        score_data = score_job_post(
            title=job["job_title"],
            company=job["company"],
            description=job.get("description", ""),
            poster_text="",
        )
        relevance = score_data.get("relevance_score", 0)

        stub = {
            "linkedin_post_url": url,
            "job_title": job["job_title"],
            "company": job["company"],
            "poster_name": job.get("poster_name", ""),
            "poster_url": job.get("poster_url"),
            "description": job.get("description", "")[:500],
            "is_easy_apply": job.get("is_easy_apply", False),
            "external_apply_url": job.get("external_apply_url"),
            "posted_ago_text": job.get("posted_ago_text", ""),
            "relevance_score": relevance,
            "reasoning": score_data.get("reasoning", ""),
            "status": "evaluating",
            "slack_ts": None,
            "discovered_at": datetime.datetime.utcnow(),
            "applied_at": None,
        }

        if relevance < MANUAL_APPLY_MIN_SCORE:
            stub["status"] = "low_score"
            await db.recent_jobs.insert_one(stub)
            low_score += 1
            continue

        if job.get("is_easy_apply") and relevance >= EASY_APPLY_MIN_SCORE:
            await ActionQueue.push(
                "auto_apply",
                "easy_apply",
                {
                    "url": url,
                    "title": job["job_title"],
                    "company": job["company"],
                    "score": relevance,
                },
                priority=2,
                is_dry_run=config.DRY_RUN,
            )
            stub["status"] = "queued_apply"
            await db.recent_jobs.insert_one(stub)
            queued_apply += 1
        else:
            ts = await send_manual_apply_alert(job, score_data)
            stub["status"] = "slack_manual"
            stub["slack_ts"] = ts
            await db.recent_jobs.insert_one(stub)
            slack_manual += 1

    return {
        "status": "done",
        "queued_apply": queued_apply,
        "slack_manual": slack_manual,
        "low_score": low_score,
        "total": len(jobs),
    }
