import re
import asyncio
import random
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth


async def react_to_post(post_url: str, headless: bool = True):
    """Navigate to a post and click the Like button."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await page.goto(post_url)
        await page.wait_for_timeout(3000)

        try:
            like_btn = page.locator(
                "button[aria-label='React Like'], button[aria-label='Like']"
            ).first
            if await like_btn.count() > 0:
                await like_btn.click()
                print(f"Liked: {post_url}")
            else:
                print("Like button not found.")
        except Exception as e:
            print(f"Failed to react: {e}")

        await page.wait_for_timeout(2000)
        await context.browser.close()


async def comment_on_post(post_url: str, comment_text: str, headless: bool = True):
    """Navigate to a post, type a comment, and submit it."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await page.goto(post_url)
        await page.wait_for_timeout(3000)

        try:
            comment_box = page.locator(
                "div[role='textbox'][aria-label*='comment'], div.ql-editor"
            ).first
            if await comment_box.count() > 0:
                await comment_box.click()
                await page.keyboard.type(comment_text, delay=random.randint(40, 80))
                await page.wait_for_timeout(1000)

                submit_btn = page.locator(
                    "button.artdeco-button--primary:has-text('Comment'), "
                    "button.artdeco-button--primary:has-text('Post')"
                ).first
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                    print(f"Commented on: {post_url}")
                else:
                    print("Submit button not found.")
            else:
                print("Comment box not found.")
        except Exception as e:
            print(f"Failed to comment: {e}")

        await page.wait_for_timeout(2000)
        await context.browser.close()


async def send_connection_request(
    profile_url: str, note_text: str = None, headless: bool = True
):
    """Navigate to a profile and send a connection request with optional note."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await page.goto(profile_url)
        await page.wait_for_timeout(3000)

        try:
            # Primary: aria-label based — stable across LinkedIn redesigns
            connect_btn = page.locator(
                "button[aria-label='Connect'], "
                "button[aria-label*='Invite'][aria-label*='connect']"
            ).first

            try:
                await connect_btn.wait_for(state="visible", timeout=8000)
            except Exception:
                pass

            # Fallback: "More" dropdown in profile header
            if await connect_btn.count() == 0:
                more_btn = page.locator(
                    "button[aria-label='More actions']"
                ).first
                if await more_btn.count() > 0:
                    await more_btn.click()
                    await page.wait_for_timeout(1000)
                    connect_btn = page.locator(
                        "div[role='dialog'], div[role='menu']"
                    ).get_by_role(
                        "menuitem", name=re.compile(r"Connect", re.IGNORECASE)
                    ).first

            if await connect_btn.count() > 0:
                await connect_btn.click()
                await page.wait_for_timeout(2000)

                # Add a note if requested
                if note_text:
                    add_note_btn = page.locator("button[aria-label='Add a note']").first
                    if await add_note_btn.count() > 0:
                        await add_note_btn.click()
                        await page.wait_for_timeout(500)
                        textarea = page.locator("textarea[name='message']").first
                        await textarea.fill(note_text[:300])
                        await page.wait_for_timeout(1000)

                # Click Send
                send_btn = page.locator(
                    "button[aria-label='Send without a note'], "
                    "button[aria-label='Send now'], "
                    "button[aria-label='Send'], "
                    "button:has-text('Send')"
                ).first
                if await send_btn.count() > 0:
                    await send_btn.click()
                    print(f"Connection request sent: {profile_url}")
                else:
                    print("Send button not found.")
            else:
                print(f"Connect button not found for: {profile_url}")
        except Exception as e:
            print(f"Failed to connect: {e}")

        await page.wait_for_timeout(2000)
        await context.browser.close()


async def repost_post(post_url: str, headless: bool = True):
    """Navigate to a post and repost it (without additional text)."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await page.goto(post_url)
        await page.wait_for_timeout(3000)

        try:
            repost_btn = page.locator(
                "button[aria-label*='Repost'], button[aria-label*='repost']"
            ).first
            if await repost_btn.count() > 0:
                await repost_btn.click()
                await page.wait_for_timeout(1000)
                confirm_btn = page.locator(
                    "div[role='menu'] span:has-text('Repost'), button:has-text('Repost')"
                ).first
                if await confirm_btn.count() > 0:
                    await confirm_btn.click()
                    print(f"Reposted: {post_url}")
                else:
                    print("Repost confirm button not found.")
            else:
                print("Repost button not found.")
        except Exception as e:
            print(f"Failed to repost: {e}")

        await page.wait_for_timeout(2000)
        await context.browser.close()
