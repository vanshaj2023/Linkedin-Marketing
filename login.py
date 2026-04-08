import asyncio
from playwright.async_api import async_playwright

STATE_FILE = "state.json"

async def manual_login():
    """
    Opens a visible browser so you can log into LinkedIn.
    Once it detects you are logged in (by seeing the feed or if you hit Enter in terminal), 
    it saves your cookies and state so future runs can be headless / automated.
    """
    print("Launching browser for manual login...")
    async with async_playwright() as p:
        # Launch headed so the user can interact
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        await page.goto("https://www.linkedin.com/login")
        
        print("\n" + "="*50)
        print("Please log into LinkedIn in the opened browser window.")
        print("Solve any CAPTCHA or 2FA if prompted.")
        print("Once you are fully logged in and on your feed, return here and press Enter.")
        print("="*50 + "\n")
        
        # We can implement a wait-for-URL logic, or just wait for the user to hit Enter.
        # Python's input() is blocking, so we use asyncio equivalent
        await asyncio.to_thread(input, "Press ENTER after you have logged in successfully...")
        
        # Verify we are somewhat logged in by checking the URL or just trust the user
        current_url = page.url
        print(f"Current URL: {current_url}")
        
        # Save state
        print(f"Saving browser state to {STATE_FILE}...")
        await context.storage_state(path=STATE_FILE)
        print("State saved successfully! You won't need to log in manually next time.")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(manual_login())
