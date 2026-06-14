"""Quick test: scrape recent jobs (last 24h) and score them."""
import asyncio
import json
from scrapers.jobs_recent import scrape_recent_jobs
from llm.service import score_job_post
from config import config


async def main():
    keywords = config.TARGET_JOB_KEYWORDS[:2]
    locations = config.TARGET_JOB_LOCATIONS[:1]
    print(f"Searching: {keywords} in {locations}\n")

    jobs = await scrape_recent_jobs(
        keywords=keywords,
        locations=locations,
        max_per_combo=5,
        headless=False,
    )
    print(f"Found {len(jobs)} jobs\n")

    for job in jobs:
        score_data = score_job_post(
            title=job["job_title"],
            company=job["company"],
            description=job.get("description", ""),
            poster_text="",
        )
        score = score_data.get("relevance_score", 0)
        ea = "Easy Apply" if job["is_easy_apply"] else "External"
        print(f"[{score:3d}] {job['job_title']} @ {job['company']} ({ea}) — {job.get('posted_ago_text','')}")
        print(f"      {job['linkedin_post_url']}")
        if job.get("external_apply_url"):
            print(f"      ATS: {job['external_apply_url']}")
        print(f"      why: {score_data.get('reasoning','')}")
        print()


asyncio.run(main())
