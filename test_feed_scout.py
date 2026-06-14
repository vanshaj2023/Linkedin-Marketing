"""Quick test: scrape the home feed and classify posts for job signals."""
import asyncio
from scrapers.feed import scrape_organic_feed
from llm.service import classify_feed_post_as_job


async def main():
    print("Scraping home feed (max 20 posts)...")
    posts = await scrape_organic_feed(max_posts=20, headless=False, enrich_urns=False)
    print(f"Got {len(posts)} posts\n")

    for post in posts:
        content = post.get("content", "")
        if len(content) < 80:
            continue

        cls = classify_feed_post_as_job(content, post.get("author_name", ""))
        if cls["is_job_post"]:
            print(f"[JOB SIGNAL] score={cls['relevance_score']:3d} | {post['author_name']}")
            print(f"  role={cls['role']} | company={cls['company']}")
            print(f"  method={cls['apply_method']} | target={cls['apply_target']}")
            print(f"  why: {cls['reasoning']}")
            print(f"  author_url: {post.get('author_url')}")
            print()
        else:
            print(f"[skip] score={cls['relevance_score']:3d} | {post['author_name']} — {content[:60]}...")


asyncio.run(main())
