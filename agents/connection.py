import datetime
import random
import inngest
from inngest_client import inngest_client
from db import db
from core.action_queue import ActionQueue
from core.circuit_breaker import CircuitBreaker
from scrapers.people import search_people
from llm.service import generate_connection_note, score_connection_profile
from config import config

TEMPLATES = ["A", "B", "C", "D", "E"]


@inngest_client.create_function(
    fn_id="connection-agent-run",
    trigger=inngest.TriggerCron(cron="0 9,18 * * *"),
    retries=2,
)
async def connection_agent_run(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Twice daily: search people -> score -> queue connect requests."""
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    all_profiles = []
    for keyword in config.TARGET_KEYWORDS[:3]:
        profiles = await step.run(
            f"search-{keyword.replace(' ', '_')}",
            search_people, keyword, 20,
        )
        all_profiles.extend(profiles)

    queued = 0
    for profile in all_profiles:
        existing = await db.connections.find_one({"linkedin_url": profile["linkedin_url"]})
        if existing:
            continue

        score = score_connection_profile(
            profile["headline"], profile["company"], profile.get("mutual_connections", 0),
        )
        if score < config.CONNECTION_RELEVANCE_THRESHOLD:
            continue

        template = random.choice(TEMPLATES)
        note = generate_connection_note(
            headline=profile["headline"],
            post_summary=profile.get("headline", ""),
            template_id=template,
        )

        await db.connections.update_one(
            {"linkedin_url": profile["linkedin_url"]},
            {"$setOnInsert": {
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
            }},
            upsert=True,
        )

        await ActionQueue.push(
            "connection", "view_profile",
            {"target_profile_url": profile["linkedin_url"]},
            priority=3, is_dry_run=config.DRY_RUN,
        )
        await ActionQueue.push(
            "connection", "connect",
            {"target_profile_url": profile["linkedin_url"], "message": note},
            priority=2, is_dry_run=config.DRY_RUN,
        )

        await db.connections.update_one(
            {"linkedin_url": profile["linkedin_url"]},
            {"$set": {
                "status": "request_sent",
                "first_contacted_at": datetime.datetime.utcnow(),
                "last_action_at": datetime.datetime.utcnow(),
            }},
        )
        queued += 1

    return {"status": "done", "queued": queued, "dry_run": config.DRY_RUN}


@inngest_client.create_function(
    fn_id="connection-acceptance-poller",
    trigger=inngest.TriggerCron(cron="0 */6 * * *"),
    retries=2,
)
async def connection_acceptance_poller(ctx: inngest.Context, step: inngest.Step) -> dict:
    """Every 6h: check pending requests, emit connection/accepted events."""
    health = await step.run("check-cb", CircuitBreaker.status)
    if health == "red":
        return {"status": "skipped", "reason": "circuit_breaker_red"}

    pending = await db.connections.find({"status": "request_sent"}).to_list(length=200)
    if not pending:
        return {"status": "done", "checked": 0, "accepted": 0}

    accepted_count = 0
    for conn in pending:
        url = conn["linkedin_url"]
        is_accepted = await step.run(
            f"check-{conn['_id']}", _check_if_connected, url,
        )
        if is_accepted:
            await db.connections.update_one(
                {"linkedin_url": url},
                {"$set": {"status": "accepted", "connected_at": datetime.datetime.utcnow()}},
            )
            await inngest_client.send(inngest.Event(
                name="connection/accepted",
                data={"linkedin_url": url, "name": conn["name"], "company": conn["company"]},
            ))
            accepted_count += 1

    return {"status": "done", "checked": len(pending), "accepted": accepted_count}


async def _check_if_connected(profile_url: str) -> bool:
    """Visit a profile and check if the connection degree shows '1st'."""
    from playwright.async_api import async_playwright
    from browser.manager import get_authenticated_context, setup_page_stealth

    try:
        async with async_playwright() as p:
            ctx = await get_authenticated_context(p, headless=True)
            page = await ctx.new_page()
            await setup_page_stealth(page)
            await page.goto(profile_url)
            await page.wait_for_timeout(3000)

            # Try multiple selectors — LinkedIn changes these frequently
            degree_selectors = [
                "span.dist-value",
                "span[class*='distance-badge']",
                "span.pvs-header__subtitle",
            ]
            degree_text = ""
            for sel in degree_selectors:
                el = page.locator(sel).first
                if await el.count() > 0:
                    degree_text = (await el.inner_text()).strip()
                    break

            # Fallback: check for "1st" anywhere in the profile header
            if not degree_text:
                header = page.locator("section.pv-top-card, div.ph5").first
                if await header.count() > 0:
                    header_text = await header.inner_text()
                    if "1st" in header_text:
                        degree_text = "1st"

            await ctx.browser.close()
            return "1st" in degree_text
    except Exception as e:
        print(f"Error checking connection for {profile_url}: {e}")
        return False
