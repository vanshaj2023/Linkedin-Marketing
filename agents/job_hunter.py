import datetime
import inngest
from inngest_client import inngest_client
from db import db
from core.action_queue import ActionQueue
from core.circuit_breaker import CircuitBreaker
from scrapers.jobs import search_jobs
from llm.service import score_job_post
from slack.bot import send_job_alert
from config import config


@inngest_client.create_function(
    fn_id="job-hunter-run",
    trigger=inngest.TriggerCron(cron="0 8,13,19 * * *"),
    retries=2,
)
async def job_hunter_run(ctx: inngest.Context, step: inngest.Step) -> dict:
    """3x daily: search jobs -> LLM score -> Slack notify + auto-comment + referral trigger."""
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    all_jobs = []
    for keyword in config.TARGET_JOB_KEYWORDS[:2]:
        for location in config.TARGET_JOB_LOCATIONS[:2]:
            jobs = await step.run(
                f"search-{keyword.replace(' ', '_')}-{location.replace(' ', '_')}",
                search_jobs, keyword, location, 10,
            )
            all_jobs.extend(jobs)

    notified = 0
    for job in all_jobs:
        existing = await db.jobs.find_one({"linkedin_post_url": job["linkedin_post_url"]})
        if existing:
            continue

        score_data = score_job_post(
            title=job["job_title"],
            company=job["company"],
            description=job.get("description", ""),
            poster_text=job.get("poster_text", ""),
        )
        relevance = score_data.get("relevance_score", 0)
        action_taken = "none"
        slack_ts = None

        if relevance >= config.JOB_SLACK_NOTIFY_THRESHOLD:
            slack_ts = await send_job_alert({**job, **score_data})
            action_taken = "slack_notified"
            notified += 1

        if score_data.get("should_comment_email") and score_data.get("comment_text"):
            await ActionQueue.push(
                "job_hunter", "comment",
                {"post_url": job["linkedin_post_url"], "message": score_data["comment_text"]},
                priority=2, is_dry_run=config.DRY_RUN,
            )
            action_taken = "commented"

        if score_data.get("company_for_referral") and relevance >= 80:
            await inngest_client.send(inngest.Event(
                name="referral/campaign.start",
                data={
                    "company": score_data["company_for_referral"],
                    "job_post_url": job["linkedin_post_url"],
                    "target_role": job["job_title"],
                    "source": "job_hunter",
                },
            ))
            action_taken = "referral_triggered"

        await db.jobs.insert_one({
            "linkedin_post_url": job["linkedin_post_url"],
            "job_title": job["job_title"],
            "company": job["company"],
            "poster_name": job.get("poster_name", ""),
            "relevance_score": relevance,
            "action_taken": action_taken,
            "comment_text": score_data.get("comment_text"),
            "slack_message_ts": slack_ts,
            "reasoning": score_data.get("reasoning", ""),
            "discovered_at": datetime.datetime.utcnow(),
            "applied": False,
            "applied_at": None,
        })

    return {"status": "done", "notified": notified, "total": len(all_jobs)}
