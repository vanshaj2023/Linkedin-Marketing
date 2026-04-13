import datetime
import random
import inngest
from inngest_client import inngest_client
from database import db
from action_queue import ActionQueue
from system_health import CircuitBreaker
from people_scraper import search_people
from llm_service import generate_connection_note, score_connection_profile
from config import config

TEMPLATES = ["A", "B", "C", "D", "E"]


@inngest_client.create_function(
    fn_id="connection-agent-run",
    trigger=inngest.TriggerCron(cron="0 9,18 * * *"),  # 9 AM and 6 PM UTC
    retries=2,
)
async def connection_agent_run(ctx: inngest.Context, step: inngest.Step) -> dict:
    """
    Twice daily: search LinkedIn for relevant people → score them → queue
    view_profile + connect actions for those above the relevance threshold.
    """
    health = await step.run("check-circuit-breaker", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    # Search across first 3 target keywords
    all_profiles = []
    for keyword in config.TARGET_KEYWORDS[:3]:
        profiles = await step.run(
            f"search-people-{keyword.replace(' ', '_')}",
            search_people,
            keyword,
            20,
        )
        all_profiles.extend(profiles)

    queued = 0
    for profile in all_profiles:
        # Skip already contacted
        existing = await db.connections.find_one({"linkedin_url": profile["linkedin_url"]})
        if existing:
            continue

        # Score relevance
        score = score_connection_profile(
            profile["headline"],
            profile["company"],
            profile.get("mutual_connections", 0),
        )
        if score < config.CONNECTION_RELEVANCE_THRESHOLD:
            continue

        # Generate personalised connection note (rotating template for A/B test)
        template = random.choice(TEMPLATES)
        note = generate_connection_note(
            headline=profile["headline"],
            post_summary=profile.get("headline", ""),  # use headline as context proxy
            template_id=template,
        )

        # Save to connections collection (first time only)
        await db.connections.update_one(
            {"linkedin_url": profile["linkedin_url"]},
            {
                "$setOnInsert": {
                    "linkedin_url": profile["linkedin_url"],
                    "name": profile["name"],
                    "headline": profile["headline"],
                    "company": profile["company"],
                    "status": "identified",
                    "source_agent": "connection",
                    "relevance_score": score,
                    "personalization_note": note,
                    "template_used": template,
                    "connected_at": None,
                    "first_contacted_at": None,
                    "last_action_at": None,
                    "tags": [],
                }
            },
            upsert=True,
        )

        # Queue: view profile first (priority 3), then send connect (priority 2)
        await ActionQueue.push(
            "connection", "view_profile",
            {"target_profile_url": profile["linkedin_url"]},
            priority=3,
            is_dry_run=config.DRY_RUN,
        )
        await ActionQueue.push(
            "connection", "connect",
            {"target_profile_url": profile["linkedin_url"], "message": note},
            priority=2,
            is_dry_run=config.DRY_RUN,
        )

        # Mark as request_sent
        await db.connections.update_one(
            {"linkedin_url": profile["linkedin_url"]},
            {
                "$set": {
                    "status": "request_sent",
                    "first_contacted_at": datetime.datetime.utcnow(),
                    "last_action_at": datetime.datetime.utcnow(),
                }
            },
        )
        queued += 1

    return {"status": "done", "queued": queued, "dry_run": config.DRY_RUN}


@inngest_client.create_function(
    fn_id="connection-acceptance-poller",
    trigger=inngest.TriggerCron(cron="0 */6 * * *"),  # Every 6 hours
    retries=2,
)
async def connection_acceptance_poller(ctx: inngest.Context, step: inngest.Step) -> dict:
    """
    Every 6 hours: check pending connection requests and emit connection/accepted
    events for newly accepted connections so the Referral Agent can react.
    """
    health = await step.run("check-circuit-breaker", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    pending = await db.connections.find({"status": "request_sent"}).to_list(length=200)
    if not pending:
        return {"status": "done", "checked": 0, "accepted": 0}

    accepted_count = 0
    for conn in pending:
        profile_url = conn["linkedin_url"]
        is_accepted = await step.run(
            f"check-connection-{conn['_id']}",
            _check_if_connected,
            profile_url,
        )
        if is_accepted:
            await db.connections.update_one(
                {"linkedin_url": profile_url},
                {"$set": {"status": "accepted", "connected_at": datetime.datetime.utcnow()}},
            )
            # Fire event — referral_agent listens for this
            await inngest_client.send(
                inngest.Event(
                    name="connection/accepted",
                    data={
                        "linkedin_url": profile_url,
                        "name": conn["name"],
                        "company": conn["company"],
                    },
                )
            )
            accepted_count += 1

    return {"status": "done", "checked": len(pending), "accepted": accepted_count}


async def _check_if_connected(profile_url: str) -> bool:
    """
    Navigates to a LinkedIn profile and checks if the degree badge shows '1st'
    (meaning we are connected).
    """
    from playwright.async_api import async_playwright
    import browser_manager
    try:
        async with async_playwright() as p:
            ctx = await browser_manager.get_authenticated_context(p, headless=True)
            page = await ctx.new_page()
            await browser_manager.setup_page_stealth(page)
            await page.goto(profile_url)
            await page.wait_for_timeout(3000)
            degree_el = page.locator("span.dist-value").first
            degree_text = (
                (await degree_el.inner_text()).strip()
                if await degree_el.count() > 0
                else ""
            )
            await ctx.browser.close()
            return "1st" in degree_text
    except Exception as e:
        print(f"Error checking connection status for {profile_url}: {e}")
        return False
