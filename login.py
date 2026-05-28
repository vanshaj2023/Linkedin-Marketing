import os
import asyncio
from playwright.async_api import async_playwright
from browser.manager import (
    get_authenticated_context,
    setup_page_stealth,
    validate_session,
    STATE_FILE,
    CONSISTENT_USER_AGENT,
)


async def manual_login():
    """Open a visible browser with consistent stealth settings for manual login.

    Uses the EXACT same fingerprint (UA, viewport, stealth patches) that the
    scrapers use, so the session cookies saved here will work seamlessly.
    """
    print("Preparing for manual login...")

    # 1. Clear old (potentially flagged) session
    if os.path.exists(STATE_FILE):
        try:
            os.remove(STATE_FILE)
            print(f"  Cleared old {STATE_FILE} (may have been flagged).")
        except Exception as e:
            print(f"  Warning: could not delete old state file: {e}")

    # 2. Launch with the SAME fingerprint used by all scrapers
    async with async_playwright() as p:
        print(f"  Launching browser (UA: ...{CONSISTENT_USER_AGENT[-30:]})")

        # We intentionally do NOT load storage_state here (file was deleted).
        # get_authenticated_context gracefully skips it when the file is absent.
        context = await get_authenticated_context(p, headless=False)
        page = await context.new_page()
        await setup_page_stealth(page)

        await page.goto("https://www.linkedin.com/login")

        print()
        print("=" * 55)
        print("  1. Log into LinkedIn in the browser window.")
        print("  2. Complete any CAPTCHA / 2FA if prompted.")
        print("  3. Wait until your FEED loads fully.")
        print("  4. Come back here and press ENTER.")
        print("=" * 55)
        print()

        await asyncio.to_thread(input, "Press ENTER after successful login... ")

        # 3. Validate the session before saving
        current_url = page.url
        print(f"  Current URL: {current_url}")

        is_valid = await validate_session(page)
        if not is_valid:
            print("  ⚠ Session does not look valid (login page detected).")
            print("  Saving anyway — please re-run if it doesn't work.")

        # 4. Save session state
        await context.storage_state(path=STATE_FILE)
        print(f"  ✓ Session saved to {STATE_FILE}")

        # 5. Quick verification — try loading the feed
        try:
            print("  Verifying session by loading feed...")
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            is_valid = await validate_session(page)
            if is_valid:
                print("  ✓ Session verified — feed loaded successfully!")
            else:
                print("  ⚠ Feed did not load. Session may be invalid.")
        except Exception as e:
            print(f"  ⚠ Feed verification failed: {e}")

        await context.browser.close()


if __name__ == "__main__":
    asyncio.run(manual_login())
