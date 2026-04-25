import asyncio
import random
from datetime import datetime

from db import db, ActionQueueItem
from core.circuit_breaker import CircuitBreaker
from core.budget import BudgetManager
from config import config

# Map action_type to budget key
BUDGET_MAP = {
    "connect": "connection_requests",
    "like": "likes",
    "comment": "comments",
    "view_profile": "profile_views",
    "search": "searches",
    "repost": "reposts",
}


class ActionQueue:
    @staticmethod
    async def push(
        agent: str,
        action_type: str,
        payload: dict,
        priority: int = 5,
        is_dry_run: bool = False,
    ):
        item = ActionQueueItem(
            agent=agent,
            action_type=action_type,
            payload=payload,
            priority=priority,
            dry_run=is_dry_run,
        )
        result = await db.action_queue.insert_one(item.model_dump(by_alias=True))
        print(f"[{agent}] Queued '{action_type}' (ID: {result.inserted_id})")
        return result.inserted_id

    @staticmethod
    async def get_next_action():
        return await db.action_queue.find_one_and_update(
            {"status": "queued"},
            {"$set": {"status": "processing"}},
            sort=[("priority", 1), ("created_at", 1)],
            return_document=True,
        )

    @staticmethod
    async def mark_done(action_id):
        await db.action_queue.update_one(
            {"_id": action_id},
            {"$set": {"status": "done", "executed_at": datetime.utcnow()}},
        )

    @staticmethod
    async def mark_failed(action_id, error_msg: str, max_retries: int = 3):
        item = await db.action_queue.find_one({"_id": action_id})
        if not item:
            return
        if item.get("retry_count", 0) < max_retries:
            await db.action_queue.update_one(
                {"_id": action_id},
                {"$inc": {"retry_count": 1}, "$set": {"status": "queued", "error": error_msg}},
            )
        else:
            await db.action_queue.update_one(
                {"_id": action_id},
                {"$set": {"status": "failed", "error": error_msg, "executed_at": datetime.utcnow()}},
            )


async def process_one_action() -> dict:
    health = await CircuitBreaker.status()
    if health == "red":
        return {"status": "halted", "reason": "circuit_breaker_red"}

    action = await ActionQueue.get_next_action()
    if not action:
        return {"status": "empty"}

    action_id = action["_id"]
    action_type = action["action_type"]
    payload = action.get("payload", {})
    is_dry_run = action.get("dry_run", config.DRY_RUN)
    budget_key = BUDGET_MAP.get(action_type)

    if budget_key:
        if not await BudgetManager.check_budget(budget_key):
            await db.action_queue.update_one(
                {"_id": action_id}, {"$set": {"status": "deferred"}}
            )
            return {"status": "deferred", "reason": f"budget_exhausted:{budget_key}"}

    try:
        if is_dry_run:
            print(f"[DRY RUN] Would execute: {action_type} -> {payload}")
            await asyncio.sleep(random.uniform(1, 3))
        else:
            await _dispatch_action(action_type, payload, health)

        if budget_key:
            await BudgetManager.increment_budget(budget_key)

        await ActionQueue.mark_done(action_id)
        return {"status": "done", "action_id": str(action_id), "action_type": action_type}

    except Exception as e:
        error_msg = str(e)
        await ActionQueue.mark_failed(action_id, error_msg)
        return {"status": "failed", "action_id": str(action_id), "error": error_msg}


async def _dispatch_action(action_type: str, payload: dict, health: str):
    from browser.interactions import react_to_post, comment_on_post, send_connection_request, repost_post
    from browser.manager import safe_sleep, get_browser_page

    # Pre-action human delay
    await safe_sleep()

    if action_type == "view_profile":
        page, context = await get_browser_page(headless=True)
        try:
            await page.goto(payload["target_profile_url"])
            await page.wait_for_timeout(random.randint(3000, 7000))
        finally:
            await context.browser.close()

    elif action_type == "connect":
        await send_connection_request(
            profile_url=payload["target_profile_url"],
            note_text=payload.get("message"),
            headless=True,
        )

    elif action_type == "like":
        await react_to_post(post_url=payload["post_url"], headless=True)

    elif action_type == "comment":
        await comment_on_post(
            post_url=payload["post_url"],
            comment_text=payload["message"],
            headless=True,
        )

    elif action_type == "repost":
        await repost_post(post_url=payload["post_url"], headless=True)

    # Post-action delay (doubled on yellow)
    delay = random.uniform(1, 4)
    if health == "yellow":
        delay *= 2
    await asyncio.sleep(delay)


async def requeue_deferred_actions() -> int:
    result = await db.action_queue.update_many(
        {"status": "deferred"}, {"$set": {"status": "queued"}}
    )
    return result.modified_count


# ── Inngest functions ────────────────────────────────────────────────────────

from inngest_client import inngest_client
import inngest as _inngest
from core.warmup import apply_warmup_budget


@inngest_client.create_function(
    fn_id="queue-processor",
    trigger=_inngest.TriggerCron(cron="*/5 * * * *"),
    retries=0,
    concurrency=[_inngest.Concurrency(limit=1)],
)
async def inngest_queue_processor(ctx: _inngest.Context, step: _inngest.Step) -> dict:
    return await step.run("process-one-action", process_one_action)


@inngest_client.create_function(
    fn_id="budget-reset",
    trigger=_inngest.TriggerCron(cron="0 0 * * *"),
    retries=1,
)
async def inngest_budget_reset(ctx: _inngest.Context, step: _inngest.Step) -> dict:
    await step.run(
        "apply-warmup-limits",
        lambda: apply_warmup_budget(week=config.WARMUP_WEEK),
    )
    requeued = await step.run("requeue-deferred", requeue_deferred_actions)
    return {"status": "done", "requeued": requeued}
