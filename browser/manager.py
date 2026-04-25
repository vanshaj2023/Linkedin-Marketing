import os
import random
import asyncio
from playwright.async_api import async_playwright, BrowserContext, Page
from playwright_stealth import stealth_async

STATE_FILE = "state.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]


def _random_viewport() -> dict:
    return {"width": random.randint(1280, 1440), "height": random.randint(720, 900)}


async def get_authenticated_context(p, headless: bool = True) -> BrowserContext:
    proxy_url = os.getenv("PROXY_URL")
    launch_options = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy_url:
        launch_options["proxy"] = {"server": proxy_url}

    browser = await p.chromium.launch(**launch_options)
    context_options = {
        "user_agent": random.choice(USER_AGENTS),
        "viewport": _random_viewport(),
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    if os.path.exists(STATE_FILE):
        context_options["storage_state"] = STATE_FILE

    return await browser.new_context(**context_options)


async def setup_page_stealth(page: Page) -> Page:
    await stealth_async(page)
    return page


async def get_browser_page(headless: bool = True) -> tuple[Page, BrowserContext]:
    """Convenience: returns a stealth-ready (page, context) pair."""
    p_instance = await async_playwright().start()
    ctx = await get_authenticated_context(p_instance, headless=headless)
    page = await ctx.new_page()
    await setup_page_stealth(page)
    return page, ctx


async def human_type(page: Page, selector: str, text: str):
    for char in text:
        await page.type(selector, char, delay=random.randint(80, 160))


async def human_click(page: Page, selector: str):
    element = page.locator(selector).first
    box = await element.bounding_box()
    if box:
        x = box["x"] + random.uniform(5, box["width"] - 5)
        y = box["y"] + random.uniform(5, box["height"] - 5)
        await page.mouse.move(x, y, steps=10)
        await asyncio.sleep(random.uniform(0.1, 0.4))
    await element.click(delay=random.randint(50, 150))


async def safe_sleep():
    """Random 2-8s delay for use before/after critical actions."""
    await asyncio.sleep(random.uniform(2.0, 8.0))
