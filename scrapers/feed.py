import re
import asyncio
from playwright.async_api import async_playwright
from browser.manager import (
    get_authenticated_context,
    setup_page_stealth,
    safe_sleep,
    validate_session,
)


URN_RE = re.compile(r"urn:li:(activity|share|ugcPost):(\d+)")

# Selector used to identify any element containing a LinkedIn post URN link.
_URN_LINK_SEL = (
    "a[href*='urn:li:activity'], "
    "a[href*='urn:li:share'], "
    "a[href*='urn:li:ugcPost']"
)


async def _is_promoted(post_element) -> bool:
    try:
        return await post_element.locator(
            "span:has-text('Promoted'), span:has-text('Sponsored')"
        ).count() > 0
    except Exception:
        return False


async def _extract_author_name(post_element) -> str:
    """Extract the author name from a feed post element using multiple strategies."""
    # Strategy 1: Visible name inside the actor span (most reliable)
    selectors = [
        "span.update-components-actor__name span[aria-hidden='true']",
        "span.update-components-actor__name",
        "span.feed-shared-actor__name span[aria-hidden='true']",
        "span.feed-shared-actor__name",
        ".update-components-actor__title span[aria-hidden='true']",
        "span[class*='actor__name']",
    ]
    for sel in selectors:
        el = post_element.locator(sel).first
        if await el.count() > 0:
            try:
                raw = (await el.inner_text()).strip()
                if raw:
                    # LinkedIn duplicates name for a11y: "Jane\nJane\n• 1st\n• 1d"
                    for line in raw.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("•"):
                            return line
            except Exception:
                continue

    # Strategy 2: Profile link text
    author_link = post_element.locator("a[href*='/in/']").first
    if await author_link.count() > 0:
        try:
            txt = (await author_link.inner_text()).strip()
            if txt:
                return txt.split("\n")[0].strip()
        except Exception:
            pass

    return "Unknown"


async def _extract_content(post_element) -> str:
    """Extract the text content of a feed post."""
    selectors = [
        "[data-testid='expandable-text-box']",
        ".update-components-text",
        ".feed-shared-update-v2__description",
        ".feed-shared-inline-show-more-text",
        "div[class*='update-components-text']",
    ]
    for sel in selectors:
        el = post_element.locator(sel).first
        if await el.count() > 0:
            try:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
            except Exception:
                continue
    return ""


async def _extract_post_data(post_element) -> dict | None:
    """Extract structured data from a single feed post element."""
    try:
        if await _is_promoted(post_element):
            return None

        post_link = post_element.locator(_URN_LINK_SEL).first
        if await post_link.count() == 0:
            return None
        href = await post_link.get_attribute("href")
        if not href:
            return None
        match = URN_RE.search(href)
        if not match:
            return None
        urn = f"urn:li:{match.group(1)}:{match.group(2)}"

        author_name = await _extract_author_name(post_element)
        content = await _extract_content(post_element)
        if not content:
            return None

        return {
            "post_id": urn,
            "post_url": f"https://www.linkedin.com/feed/update/{urn}/",
            "author_name": author_name,
            "content": content,
        }
    except Exception:
        return None


def _post_container_selectors(context: str = "feed") -> str:
    """Return the CSS selectors for post containers based on page context.

    LinkedIn uses different container structures on different pages:
    - Feed: posts are in <li> elements or div.feed-shared-update-v2
    - Search results: posts are in div[role='listitem']
    - Activity page: posts are in <li> elements
    """
    if context == "search":
        return "div[role='listitem']"
    # For feed and activity pages, try multiple container types
    return "li, div.feed-shared-update-v2, div[data-urn]"


async def _find_post_elements(page, context: str = "feed") -> list:
    """Find all post elements on the current page."""
    container_sel = _post_container_selectors(context)
    elements = await page.locator(container_sel).filter(
        has=page.locator(_URN_LINK_SEL)
    ).all()
    return elements


async def scrape_hiring_posts(
    keyword: str = "hiring", max_posts: int = 3, headless: bool = True
) -> list:
    """Search LinkedIn content by keyword and return matching posts."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        url = (
            f"https://www.linkedin.com/search/results/content/"
            f"?keywords={keyword}&origin=GLOBAL_SEARCH_HEADER"
        )
        await safe_sleep()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Validate session after navigation
        if not await validate_session(page):
            print("Session invalid — redirected to login.")
            await context.browser.close()
            return []

        for _ in range(4):
            posts = await _find_post_elements(page, context="search")
            if posts:
                try:
                    await posts[-1].scroll_into_view_if_needed()
                except Exception:
                    pass
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(2000)

        post_elements = await _find_post_elements(page, context="search")

        results = []
        for el in post_elements:
            if len(results) >= max_posts:
                break
            data = await _extract_post_data(el)
            if data:
                results.append(data)

        await context.browser.close()
        return results


async def scrape_organic_feed(
    max_posts: int = 5, headless: bool = True
) -> list:
    """Scroll the main feed and extract organic posts."""
    results = []
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await safe_sleep()
        await page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(5000)

        # Validate session after navigation
        if not await validate_session(page):
            print("Session invalid — redirected to login.")
            await context.browser.close()
            return []

        # Click the main feed area to ensure focus
        try:
            main = page.locator("main[role='main']").first
            if await main.count() > 0:
                await main.click(position={"x": 50, "y": 50})
                await page.wait_for_timeout(500)
        except Exception:
            pass

        # Scroll to load more posts
        for _ in range(8):
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1500)

        post_elements = await _find_post_elements(page, context="feed")

        for el in post_elements:
            if len(results) >= max_posts:
                break
            data = await _extract_post_data(el)
            if data:
                results.append(data)

        await context.browser.close()
    return results


async def scrape_user_latest_post(
    profile_url: str, headless: bool = True
) -> dict | None:
    """Fetch the most recent post from a user's activity page."""
    activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await safe_sleep()
        await page.goto(
            activity_url, wait_until="domcontentloaded", timeout=60000
        )
        await page.wait_for_timeout(4000)

        post_elements = await _find_post_elements(page, context="feed")
        result = None
        if post_elements:
            result = await _extract_post_data(post_elements[0])

        await context.browser.close()
    return result


if __name__ == "__main__":
    posts = asyncio.run(scrape_hiring_posts("hiring", 3, headless=False))
    print(posts)
