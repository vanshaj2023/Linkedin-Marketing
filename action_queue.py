import asyncio
import random
from datetime import datetime
from database import db, ActionQueueItem


class ActionQueue:
    @staticmethod
    async def push(agent: str, action_type: str, payload: dict, priority: int = 5, is_dry_run: bool = False):
        """Push a new action onto the central queue."""
        item = ActionQueueItem(
            agent=agent,
            action_type=action_type,
            payload=payload,
            priority=priority,
            dry_run=is_dry_run
        )
        result = await db.action_queue.insert_one(item.model_dump(by_alias=True))
        print(f"[{agent}] Queued action '{action_type}' (ID: {result.inserted_id})")
        return result.inserted_id

    @staticmethod
    async def get_next_action():
        """Pulls the next highest priority queued action."""
        item = await db.action_queue.find_one_and_update(
            {"status": "queued"},
            {"$set": {"status": "processing"}},
            sort=[("priority", 1), ("created_at", 1)],
            return_document=True
        )
        return item

    @staticmethod
    async def mark_done(action_id):
        """Mark action as successfully completed."""
        await db.action_queue.update_one(
            {"_id": action_id},
            {"$set": {"status": "done", "executed_at": datetime.utcnow()}}
        )

    @staticmethod
    async def mark_failed(action_id, error_msg: str, max_retries: int = 3):
        """Mark action as failed, or increment retry count and push back to queued."""
        item = await db.action_queue.find_one({"_id": action_id})
        if item:
            if item.get("retry_count", 0) < max_retries:
                await db.action_queue.update_one(
                    {"_id": action_id},
                    {
                        "$inc": {"retry_count": 1},
                        "$set": {"status": "queued", "error": error_msg}
                    }
                )
            else:
                await db.action_queue.update_one(
                    {"_id": action_id},
                    {
                        "$set": {"status": "failed", "error": error_msg, "executed_at": datetime.utcnow()}
                    }
                )


from system_health import CircuitBreaker, BudgetManager
from config import config

# Map action_type to budget keys
BUDGET_MAP = {
    "connect": "connection_requests",
    "like": "likes",
    "comment": "comments",
    "view_profile": "profile_views",
    "search": "searches",
    "repost": "reposts"
}


async def process_one_action() -> dict:
    """
    Pulls the next queued action and executes it via Playwright.
    Returns a result dict with status.
    Called by the Inngest queue-processor cron function every 5 minutes.
    """
    # 1. Check Circuit Breaker
    health = await CircuitBreaker.status()
    if health == "red":
        return {"status": "halted", "reason": "circuit_breaker_red"}

    # 2. Pull next action
    action = await ActionQueue.get_next_action()
    if not action:
        return {"status": "empty"}

    action_id = action["_id"]
    action_type = action["action_type"]
    payload = action.get("payload", {})
    is_dry_run = action.get("dry_run", config.DRY_RUN)
    budget_key = BUDGET_MAP.get(action_type)

    # 3. Check budget
    if budget_key:
        has_budget = await BudgetManager.check_budget(budget_key)
        if not has_budget:
            await db.action_queue.update_one(
                {"_id": action_id},
                {"$set": {"status": "deferred"}}
            )
            return {"status": "deferred", "reason": f"budget_exhausted:{budget_key}"}

    # 4. Execute via Playwright (or skip on dry_run)
    try:
        if is_dry_run:
            print(f"[DRY RUN] Would execute: {action_type} → {payload}")
            await asyncio.sleep(random.uniform(1, 3))
        else:
            await _dispatch_playwright_action(action_type, payload, health)

        if budget_key:
            await BudgetManager.increment_budget(budget_key)

        await ActionQueue.mark_done(action_id)
        return {"status": "done", "action_id": str(action_id), "action_type": action_type}

    except Exception as e:
        error_msg = str(e)
        await ActionQueue.mark_failed(action_id, error_msg)
        return {"status": "failed", "action_id": str(action_id), "error": error_msg}


async def _dispatch_playwright_action(action_type: str, payload: dict, health: str):
    """Routes action_type to the correct Playwright function from interactions.py."""
    from interactions import react_to_post, comment_on_post, send_connection_request, repost_post
    from browser_manager import safe_sleep

    # Pre-action human delay (2–8 seconds per plan)
    await safe_sleep()

    if action_type == "view_profile":
        from playwright.async_api import async_playwright
        import browser_manager as bm
        async with async_playwright() as p:
            ctx = await bm.get_authenticated_context(p, headless=True)
            page = await ctx.new_page()
            await bm.setup_page_stealth(page)
            await page.goto(payload["target_profile_url"])
            await page.wait_for_timeout(random.randint(3000, 7000))
            await ctx.browser.close()

    elif action_type == "connect":
        await send_connection_request(
            profile_url=payload["target_profile_url"],
            note_text=payload.get("message"),
            headless=True
        )

    elif action_type == "like":
        await react_to_post(post_url=payload["post_url"])

    elif action_type == "comment":
        await comment_on_post(
            post_url=payload["post_url"],
            comment_text=payload["message"],
            headless=True
        )

    elif action_type == "repost":
        await repost_post(post_url=payload["post_url"])

    # Post-action delay (1–4 seconds; doubled on yellow)
    delay = random.uniform(1, 4)
    if health == "yellow":
        delay *= 2
    await asyncio.sleep(delay)


async def _requeue_deferred_actions() -> int:
    """Sets all deferred actions back to queued so they're retried today."""
    result = await db.action_queue.update_many(
        {"status": "deferred"},
        {"$set": {"status": "queued"}}
    )
    return result.modified_count


# ── Inngest functions ─────────────────────────────────────────────────────────
# Imported here at module level so main.py can collect them.
from inngest_client import inngest_client
import inngest as _inngest
from warmup import apply_warmup_budget


@inngest_client.create_function(
    fn_id="queue-processor",
    trigger=_inngest.TriggerCron(cron="*/5 * * * *"),  # Every 5 minutes
    retries=0,  # No retry — the next cron tick will pick up any remaining actions
    concurrency=[_inngest.Concurrency(limit=1)],  # Never run two processors at once
)
async def inngest_queue_processor(ctx: _inngest.Context, step: _inngest.Step) -> dict:
    """Processes one action from the MongoDB queue every 5 minutes via Playwright."""
    result = await step.run("process-one-action", process_one_action)
    return result


@inngest_client.create_function(
    fn_id="budget-reset",
    trigger=_inngest.TriggerCron(cron="0 0 * * *"),  # Midnight UTC daily
    retries=1,
)
async def inngest_budget_reset(ctx: _inngest.Context, step: _inngest.Step) -> dict:
    """Resets daily budget counters at midnight and re-applies warmup limits."""
    await step.run(
        "apply-warmup-limits",
        lambda: apply_warmup_budget(week=config.WARMUP_WEEK)
    )
    requeued = await step.run("requeue-deferred", _requeue_deferred_actions)
    return {"status": "done", "requeued": requeued}
