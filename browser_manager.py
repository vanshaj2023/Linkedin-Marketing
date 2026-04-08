import os
from playwright.async_api import async_playwright, Browser, BrowserContext

STATE_FILE = "state.json"

async def get_authenticated_context(p, headless: bool = True) -> BrowserContext:
    """
    Launches a browser and returns a context.
    If state.json exists, it loads the saved session cookies.
    """
    browser = await p.chromium.launch(headless=headless)
    
    if os.path.exists(STATE_FILE):
        print(f"Loading cached session from {STATE_FILE}...")
        context = await browser.new_context(storage_state=STATE_FILE)
    else:
        print("No cached session found. You may need to run login.py first.")
        context = await browser.new_context()
        
    return context

# Example usage/tester
async def test_manager():
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=False)
        page = await context.new_page()
        
        print("Navigating to LinkedIn feed...")
        await page.goto("https://www.linkedin.com/feed/")
        
        # Check if we got redirected to login
        if "login" in page.url or "checkpoint" in page.url:
            print("WARNING: We are not logged in or hit a checkpoint! Run login.py again.")
        else:
            print("Successfully loaded the feed using cached credentials!")
            
        await page.close()
        await context.browser.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_manager())
