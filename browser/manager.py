import os
import random
import asyncio
from playwright.async_api import async_playwright, BrowserContext, Page, Playwright
from playwright_stealth import stealth_async

STATE_FILE = "state.json"

# ── Consistent fingerprint ────────────────────────────────────────────────────
# CRITICAL: Use the SAME UA/viewport for both login and scraping.
# LinkedIn's HUMAN Security (PerimeterX) binds session cookies to the browser
# fingerprint. Changing UA or viewport between sessions = instant flag.
CONSISTENT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
CONSISTENT_VIEWPORT = {"width": 1366, "height": 768}


async def get_authenticated_context(p: Playwright, headless: bool = True) -> BrowserContext:
    """Launch browser and return a context with consistent fingerprint + saved session."""
    proxy_url = os.getenv("PROXY_URL")
    launch_options: dict = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if proxy_url:
        launch_options["proxy"] = {"server": proxy_url}

    browser = await p.chromium.launch(**launch_options)

    context_options: dict = {
        "user_agent": CONSISTENT_USER_AGENT,
        "viewport": CONSISTENT_VIEWPORT,
        "locale": "en-US",
        "timezone_id": "Asia/Calcutta",
        "color_scheme": "light",
    }
    if os.path.exists(STATE_FILE):
        context_options["storage_state"] = STATE_FILE

    return await browser.new_context(**context_options)


async def setup_page_stealth(page: Page) -> Page:
    """Apply stealth patches to avoid navigator.webdriver detection."""
    await stealth_async(page)
    return page


async def get_browser_page(headless: bool = True) -> tuple[Page, BrowserContext, Playwright]:
    """Convenience: returns a stealth-ready (page, context, playwright_instance) tuple.

    The caller MUST close both context.browser and playwright_instance when done:
        await context.browser.close()
        await p_instance.stop()
    """
    p_instance = await async_playwright().start()
    ctx = await get_authenticated_context(p_instance, headless=headless)
    page = await ctx.new_page()
    await setup_page_stealth(page)
    return page, ctx, p_instance


async def human_type(page: Page, selector: str, text: str):
    """Type text character-by-character with random delays to mimic human typing."""
    for char in text:
        await page.type(selector, char, delay=random.randint(80, 160))


async def human_click(page: Page, selector: str):
    """Click an element with realistic mouse movement and timing."""
    element = page.locator(selector).first
    box = await element.bounding_box()
    if box:
        x = box["x"] + random.uniform(5, box["width"] - 5)
        y = box["y"] + random.uniform(5, box["height"] - 5)
        await page.mouse.move(x, y, steps=random.randint(8, 15))
        await asyncio.sleep(random.uniform(0.1, 0.4))
    await element.click(delay=random.randint(50, 150))


async def safe_sleep(min_sec: float = 2.0, max_sec: float = 8.0):
    """Random delay for use before/after critical actions."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


async def validate_session(page: Page) -> bool:
    """Check whether the current session is still valid.

    Returns True if the page is on a logged-in LinkedIn page.
    Returns False if redirected to login, authwall, or a checkpoint.
    """
    url = page.url
    if any(seg in url for seg in ("/login", "/authwall", "/checkpoint")):
        return False
    # Check for login form elements
    login_fields = await page.locator("input#username, input#password").count()
    if login_fields > 0:
        return False
    return True
