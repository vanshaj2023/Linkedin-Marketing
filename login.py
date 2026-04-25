import asyncio
from playwright.async_api import async_playwright

STATE_FILE = "state.json"


async def manual_login():
    """Open a visible browser for manual LinkedIn login. Saves session for reuse."""
    print("Launching browser for manual login...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://www.linkedin.com/login")

        print("\n" + "=" * 50)
        print("Log into LinkedIn in the browser window.")
        print("Complete any CAPTCHA or 2FA if prompted.")
        print("Once on your feed, come back here and press Enter.")
        print("=" * 50 + "\n")

        await asyncio.to_thread(input, "Press ENTER after login...")

        print(f"Current URL: {page.url}")
        await context.storage_state(path=STATE_FILE)
        print(f"Session saved to {STATE_FILE}.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(manual_login())
