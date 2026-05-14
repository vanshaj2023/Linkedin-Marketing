import re
import asyncio
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


URN_RE = re.compile(r"urn:li:(activity|share|ugcPost):(\d+)")


async def _is_promoted(post_element) -> bool:
    try:
        return await post_element.locator(
            "span:has-text('Promoted'), span:has-text('Sponsored')"
        ).count() > 0
    except Exception:
        return False


async def _extract_author_name(post_element) -> str:
    # Preferred: visible name inside the actor span
    visible = post_element.locator(
        "span.update-components-actor__name span[aria-hidden='true']"
    ).first
    if await visible.count() > 0:
        txt = (await visible.inner_text()).strip()
        if txt:
            return txt

    actor_el = post_element.locator(
        "span.update-components-actor__name, span[class*='actor__name']"
    ).first
    if await actor_el.count() > 0:
        raw = (await actor_el.inner_text()).strip()
        # LinkedIn duplicates name for a11y: "Jane\nJane\n• 1st\n• 1d"
        for line in raw.split("\n"):
            line = line.strip()
            if line and not line.startswith("•"):
                return line

    author_link = post_element.locator("a[href*='/in/']").first
    if await author_link.count() > 0:
        return (await author_link.inner_text()).strip().split("\n")[0].strip()
    return "Unknown"


async def _extract_content(post_element) -> str:
    selectors = [
        "[data-testid='expandable-text-box']",
        ".update-components-text",
        ".feed-shared-update-v2__description",
        ".feed-shared-inline-show-more-text",
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

        post_link = post_element.locator(
            "a[href*='urn:li:activity'], a[href*='urn:li:share'], a[href*='urn:li:ugcPost']"
        ).first
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


async def scrape_hiring_posts(keyword: str = "hiring", max_posts: int = 3, headless: bool = True) -> list:
    """Search LinkedIn content by keyword and return matching posts."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        url = f"https://www.linkedin.com/search/results/content/?keywords={keyword}&origin=GLOBAL_SEARCH_HEADER"
        await safe_sleep()
        await page.goto(url)
        await page.wait_for_timeout(5000)

        for _ in range(4):
            posts = await page.locator("div[role='listitem']").filter(
                has=page.locator(
                    "a[href*='urn:li:activity'], a[href*='urn:li:share'], a[href*='urn:li:ugcPost']"
                )
            ).all()
            if posts:
                try:
                    await posts[-1].scroll_into_view_if_needed()
                except Exception:
                    pass
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(2000)

        post_elements = await page.locator("div[role='listitem']").filter(
            has=page.locator(
                "a[href*='urn:li:activity'], a[href*='urn:li:share'], a[href*='urn:li:ugcPost']"
            )
        ).all()

        results = []
        for el in post_elements:
            if len(results) >= max_posts:
                break
            data = await _extract_post_data(el)
            if data:
                results.append(data)

        await context.browser.close()
        return results


async def scrape_organic_feed(max_posts: int = 5, headless: bool = True) -> list:
    """Scroll the main feed and extract organic posts."""
    results = []
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await safe_sleep()
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Click the actual feed main element rather than guessing viewport coords
        try:
            main = page.locator("main[role='main']").first
            if await main.count() > 0:
                await main.click(position={"x": 50, "y": 50})
                await page.wait_for_timeout(500)
        except Exception:
            pass

        for _ in range(8):
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1500)

        post_elements = await page.locator("li").filter(
            has=page.locator(
                "a[href*='urn:li:activity'], a[href*='urn:li:share'], a[href*='urn:li:ugcPost']"
            )
        ).all()

        for el in post_elements:
            if len(results) >= max_posts:
                break
            data = await _extract_post_data(el)
            if data:
                results.append(data)

        await context.browser.close()
    return results


async def scrape_user_latest_post(profile_url: str, headless: bool = True) -> dict | None:
    """Fetch the most recent post from a user's activity page."""
    activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await safe_sleep()
        await page.goto(activity_url)
        await page.wait_for_timeout(4000)

        post_elements = await page.locator("li").filter(
            has=page.locator(
                "a[href*='urn:li:activity'], a[href*='urn:li:share'], a[href*='urn:li:ugcPost']"
            )
        ).all()
        result = None
        if post_elements:
            result = await _extract_post_data(post_elements[0])

        await context.browser.close()
    return result


if __name__ == "__main__":
    posts = asyncio.run(scrape_hiring_posts("hiring", 3, headless=False))
    print(posts)
