import re
import asyncio
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


async def _extract_post_data(post_element) -> dict | None:
    """Extract structured data from a single feed post element."""
    try:
        post_link = post_element.locator("a[href*='/feed/update/urn:li:activity']").first
        href = await post_link.get_attribute("href")
        if not href:
            return None
        match = re.search(r"urn:li:activity:(\d+)", href)
        if not match:
            return None
        urn = f"urn:li:activity:{match.group(1)}"

        author_link = post_element.locator("a[href*='/in/']").first
        author_name = (
            (await author_link.inner_text()).strip().split("\n")[0].strip()
            if await author_link.count() > 0
            else "Unknown"
        )

        content_el = post_element.locator("[data-testid='expandable-text-box']")
        content = await content_el.inner_text() if await content_el.count() > 0 else ""
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

        # Scroll to load posts
        for _ in range(4):
            posts = await page.locator("div[role='listitem']").filter(
                has=page.locator("a[href*='/feed/update/urn:li:activity']")
            ).all()
            if posts:
                try:
                    await posts[-1].scroll_into_view_if_needed()
                except Exception:
                    pass
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(2000)

        post_elements = await page.locator("div[role='listitem']").filter(
            has=page.locator("a[href*='/feed/update/urn:li:activity']")
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
        await page.goto("https://www.linkedin.com/feed/")
        await page.wait_for_timeout(5000)

        for _ in range(3):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight / 3)")
            await page.wait_for_timeout(2000)

        post_elements = await page.locator("div.feed-shared-update-v2").all()
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

        post_elements = await page.locator("div.feed-shared-update-v2").all()
        result = None
        if post_elements:
            result = await _extract_post_data(post_elements[0])

        await context.browser.close()
    return result


if __name__ == "__main__":
    posts = asyncio.run(scrape_hiring_posts("hiring", 3, headless=False))
    print(posts)
