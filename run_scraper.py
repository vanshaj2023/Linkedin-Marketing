"""Entry point for GitHub Actions workflow — reads TASK env var and runs the right scraper."""
import asyncio
import json
import os
from pathlib import Path

Path("output").mkdir(exist_ok=True)

TASK = os.getenv("TASK", "feed")
KEYWORD = os.getenv("KEYWORD", "hiring")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "5"))


async def main():
    if TASK == "feed":
        from scrapers.feed import scrape_organic_feed
        results = await scrape_organic_feed(max_posts=MAX_RESULTS)

    elif TASK == "hiring":
        from scrapers.feed import scrape_hiring_posts
        results = await scrape_hiring_posts(keyword=KEYWORD, max_posts=MAX_RESULTS)

    elif TASK == "people":
        from scrapers.people import scrape_people_search
        results = await scrape_people_search(keyword=KEYWORD, max_results=MAX_RESULTS)

    elif TASK == "jobs":
        from scrapers.jobs import scrape_jobs
        results = await scrape_jobs(keyword=KEYWORD, max_jobs=MAX_RESULTS)

    else:
        raise ValueError(f"Unknown TASK: {TASK}")

    out_path = Path("output") / f"{TASK}_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Saved {len(results)} results → {out_path}")


asyncio.run(main())
