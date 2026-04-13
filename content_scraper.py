import asyncio
import re
from playwright.async_api import async_playwright
import browser_manager

async def extract_post_data(post_element):
    """Refactored extraction logic for reusable parsing."""
    try:
        post_link = post_element.locator("a[href*='/feed/update/urn:li:activity']").first
        href = await post_link.get_attribute("href")
        if not href: return None
        
        match = re.search(r"urn:li:activity:(\d+)", href)
        if not match: return None
        urn = f"urn:li:activity:{match.group(1)}"
        
        author_link = post_element.locator("a[href*='/in/']").first
        author_name = await author_link.inner_text() if await author_link.count() > 0 else "Unknown Name"
        author_name = author_name.strip().split('\n')[0].strip()
        
        content_locator = post_element.locator("[data-testid='expandable-text-box']")
        content_text = await content_locator.inner_text() if await content_locator.count() > 0 else ""
        
        if not content_text: return None

        return {
            "post_id": urn,
            "post_url": f"https://www.linkedin.com/feed/update/{urn}/",
            "author_name": author_name,
            "content": content_text
        }
    except Exception:
        return None

async def scrape_organic_feed(max_posts=5, headless=True):
    """
    Scrolls the main feed to extract organic content from people in the network.
    """
    results = []
    async with async_playwright() as p:
        context = await browser_manager.get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await browser_manager.setup_page_stealth(page)
        
        print("Navigating to feed...")
        await browser_manager.safe_sleep()
        await page.goto("https://www.linkedin.com/feed/")
        await page.wait_for_timeout(5000)
        
        for _ in range(3):
            await page.evaluate('window.scrollBy(0, document.body.scrollHeight / 3)')
            await page.wait_for_timeout(2000)
            
        post_elements = await page.locator("div.feed-shared-update-v2").all()
        for element in post_elements:
            if len(results) >= max_posts: break
            data = await extract_post_data(element)
            if data:
                results.append(data)
                
        await context.browser.close()
    return results

async def scrape_user_latest_post(profile_url: str, headless=True):
    """
    Goes to a user's recent activity page to pull their latest post to interact with.
    """
    # ensure it's pointing to recent activity
    activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
    result = None
    
    async with async_playwright() as p:
        context = await browser_manager.get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await browser_manager.setup_page_stealth(page)
        
        print(f"Navigating to activity: {activity_url}")
        await browser_manager.safe_sleep()
        await page.goto(activity_url)
        await page.wait_for_timeout(4000)
        
        post_elements = await page.locator("div.feed-shared-update-v2").all()
        if post_elements:
            # Get the very first (most recent) post
            result = await extract_post_data(post_elements[0])
            
        await context.browser.close()
    return result

if __name__ == "__main__":
    # Test organic feed
    res = asyncio.run(scrape_organic_feed(3, headless=False))
    print(res)
