import asyncio
from datetime import datetime, timedelta
import random

from database import db
from system_health import BudgetManager, CircuitBreaker
from action_queue import ActionQueue
from content_scraper import scrape_organic_feed, scrape_user_latest_post
from content_scorer import score_post_for_repost, generate_thoughtful_comment
from slack_bot import send_repost_digest

class ContentAgent:
    def __init__(self, target_domain: str, is_dry_run: bool = False):
        self.target_domain = target_domain
        self.is_dry_run = is_dry_run

    async def run_repost_suggestions(self):
        """SUB-TASK A: Scanning Feed for Reposts."""
        print("Starting Content Agent: Repost Scanner...")
        
        health = await CircuitBreaker.status()
        if health != "green":
            print(f"Agent Aborting: Circuit breaker is {health}.")
            return
            
        posts = await scrape_organic_feed(max_posts=8, headless=True)
        print(f"Scraped {len(posts)} organic feed posts.")
        
        digest_candidates = []
        for p in posts:
            eval_dict = await score_post_for_repost(p["content"], p["author_name"], self.target_domain)
            
            if eval_dict.get("score", 0) >= 70:
                digest_candidates.append({
                    "score": eval_dict["score"],
                    "author_name": p["author_name"],
                    "content": p["content"],
                    "post_url": p["post_url"],
                    "reasoning": eval_dict["reasoning"],
                    "suggested_caption": eval_dict.get("suggested_repost_caption", "")
                })
                
                # Auto-repost logic
                if eval_dict.get("auto_repost"):
                    print(f"High score ({eval_dict['score']})! Auto-requesting a repost.")
                    await ActionQueue.push(
                        agent="content",
                        action_type="repost",
                        payload={"post_url": p["post_url"], "caption": eval_dict.get("suggested_repost_caption", "")},
                        priority=3,
                        is_dry_run=self.is_dry_run
                    )
                    
        # Send digest to slack
        if digest_candidates:
            await send_repost_digest(digest_candidates)
            print("Sent digest to Slack.")

    async def run_auto_reactions(self):
        """SUB-TASK B: Engaging with Engage List targets."""
        print("Starting Content Agent: Auto-Reactions...")
        
        health = await CircuitBreaker.status()
        if health != "green":
            return
            
        cursor = db.engage_list.find({})
        
        async for target in cursor:
            # Check timing rules
            last_engaged = target.get("last_engaged_at")
            if last_engaged:
                if datetime.utcnow() - last_engaged < timedelta(hours=24):
                    continue # Too soon
            
            print(f"Checking timeline for {target['name']}...")
            latest_post = await scrape_user_latest_post(target["linkedin_url"], headless=True)
            
            if not latest_post:
                continue
                
            # If we've already liked THIS post recently, skip (assuming we track last_post_url)
            if target.get("last_post_url") == latest_post["post_url"]:
                continue
                
            # Push a like
            if await BudgetManager.check_budget("likes"):
                await ActionQueue.push(
                    agent="content",
                    action_type="like",
                    payload={"post_url": latest_post["post_url"]},
                    priority=4,
                    is_dry_run=self.is_dry_run
                )
                
                # Update DB state 
                if not self.is_dry_run:
                    await db.engage_list.update_one(
                        {"_id": target["_id"]},
                        {"$set": {
                            "last_engaged_at": datetime.utcnow(),
                            "last_post_url": latest_post["post_url"]
                        }, "$inc": {"engagement_count": 1}}
                    )

            # Check if > 72 hours and if we have comments enabled for them
            if target.get("auto_comment", False) and await BudgetManager.check_budget("comments"):
                if not last_engaged or datetime.utcnow() - last_engaged >= timedelta(hours=72):
                    comment_eval = await generate_thoughtful_comment(latest_post["content"], latest_post["author_name"])
                    if comment_eval.get("is_good_hook") and comment_eval.get("comment_text"):
                        print(f"Generated thoughtful comment for {target['name']}.")
                        await ActionQueue.push(
                            agent="content",
                            action_type="comment",
                            payload={
                                "post_url": latest_post["post_url"], 
                                "message": comment_eval["comment_text"]
                            },
                            priority=2,
                            is_dry_run=self.is_dry_run
                        )

async def main():
    agent = ContentAgent(
        target_domain="Machine Learning and AI Engineering",
        is_dry_run=True
    )
    await agent.run_repost_suggestions()
    await agent.run_auto_reactions()

if __name__ == "__main__":
    asyncio.run(main())
