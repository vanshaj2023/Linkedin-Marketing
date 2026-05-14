import re
import random
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth


URN_RE = re.compile(r"urn:li:[a-zA-Z]+:\d+")


async def _find_post_container(page, post_url: str):
    """Scope locators to the specific post matching the URN in post_url."""
    m = URN_RE.search(post_url)
    if not m:
        return page
    urn = m.group(0)
    container = page.locator("div").filter(
        has=page.locator(f"a[href*='{urn}']")
    ).first
    if await container.count() > 0:
        return container
    return page


async def _wait_enabled(page, locator, timeout: int = 5000):
    handle = await locator.element_handle()
    if not handle:
        return
    await page.wait_for_function(
        "el => el && el.getAttribute('aria-disabled') !== 'true' && !el.disabled",
        arg=handle,
        timeout=timeout,
    )


async def react_to_post(post_url: str, headless: bool = True) -> dict:
    """Navigate to a post and click the Like button. Idempotent and scoped."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        result = {"ok": False, "reason": "unknown"}
        try:
            await page.goto(post_url)
            await page.wait_for_timeout(3000)

            scope = await _find_post_container(page, post_url)

            # Already-liked detection
            unlike_btn = scope.locator("button[aria-label='React Unlike']").first
            if await unlike_btn.count() > 0:
                result = {"ok": True, "reason": "already_liked"}
                print(f"Already liked: {post_url}")
            else:
                like_btn = scope.locator("button[aria-label='React Like']").first
                if await like_btn.count() == 0:
                    like_btn = scope.locator("button[aria-label='Like']").first

                if await like_btn.count() == 0:
                    print("Like button not found.")
                    result = {"ok": False, "reason": "button_not_found"}
                else:
                    pressed = await like_btn.get_attribute("aria-pressed")
                    if pressed == "true":
                        result = {"ok": True, "reason": "already_liked"}
                        print(f"Already liked: {post_url}")
                    else:
                        await like_btn.click()
                        await page.wait_for_timeout(1500)
                        print(f"Liked: {post_url}")
                        result = {"ok": True, "reason": "liked"}
        except Exception as e:
            print(f"Failed to react: {e}")
            result = {"ok": False, "reason": f"exception:{e}"}

        await page.wait_for_timeout(1500)
        await context.browser.close()
        return result


async def comment_on_post(post_url: str, comment_text: str, headless: bool = True) -> dict:
    """Navigate to a post, type a comment, and submit it. Scoped to the right post."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        result = {"ok": False, "reason": "unknown"}
        try:
            await page.goto(post_url)
            await page.wait_for_timeout(3000)

            scope = await _find_post_container(page, post_url)

            # The comment form may not be inside `scope` (it's often a sibling of the post body);
            # fall back to page-level if we can't find it in scope.
            comment_box = scope.locator(
                "div.comments-comment-box__form div[contenteditable='true'], "
                "div.comments-comment-box div[contenteditable='true']"
            ).first
            if await comment_box.count() == 0:
                comment_box = page.locator(
                    "div.comments-comment-box__form div[contenteditable='true'], "
                    "div.comments-comment-box div[contenteditable='true'], "
                    "div.ql-editor[contenteditable='true']"
                ).first

            if await comment_box.count() == 0:
                print("Comment box not found.")
                result = {"ok": False, "reason": "box_not_found"}
            else:
                await comment_box.click()
                await page.keyboard.type(comment_text, delay=random.randint(40, 80))
                await page.wait_for_timeout(800)

                # Scope submit to the same form when possible
                form = page.locator("form.comments-comment-box__form").first
                submit_btn = form.locator(
                    "button.comments-comment-box__submit-button, "
                    "button.artdeco-button--primary:has-text('Comment'), "
                    "button.artdeco-button--primary:has-text('Post')"
                ).first
                if await submit_btn.count() == 0:
                    submit_btn = page.locator(
                        "button.artdeco-button--primary:has-text('Comment'), "
                        "button.artdeco-button--primary:has-text('Post')"
                    ).first

                if await submit_btn.count() == 0:
                    print("Submit button not found.")
                    result = {"ok": False, "reason": "submit_not_found"}
                else:
                    try:
                        await _wait_enabled(page, submit_btn, timeout=5000)
                    except Exception:
                        pass
                    await submit_btn.click()
                    await page.wait_for_timeout(2000)
                    print(f"Commented on: {post_url}")
                    result = {"ok": True, "reason": "commented"}
        except Exception as e:
            print(f"Failed to comment: {e}")
            result = {"ok": False, "reason": f"exception:{e}"}

        await page.wait_for_timeout(1500)
        await context.browser.close()
        return result


async def send_connection_request(
    profile_url: str, note_text: str = None, headless: bool = True
) -> dict:
    """Navigate to a profile and send a connection request with optional note.

    Returns: {"ok": bool, "reason": str}
        reason ∈ {"sent", "pending", "already_connected", "button_not_found",
                  "weekly_limit", "send_failed", "exception:..."}
    """
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        result = {"ok": False, "reason": "unknown"}
        try:
            await page.goto(profile_url)
            try:
                await page.wait_for_selector("main", state="visible", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            top = page.locator(
                "section.pv-top-card, section[data-member-id], section.artdeco-card"
            ).first
            scope = top if await top.count() > 0 else page

            # State detection
            if await scope.locator("button[aria-label*='Pending']").count() > 0:
                print(f"Already pending: {profile_url}")
                result = {"ok": False, "reason": "pending"}
                await context.browser.close()
                return result

            direct_connect = scope.locator(
                "button[aria-label='Connect'], "
                "button[aria-label*='Invite'][aria-label*='connect']"
            ).first

            if await direct_connect.count() == 0:
                # Try the More overflow menu
                more_btn = scope.locator(
                    "button[aria-label='More actions'], button[aria-label='More']"
                ).first
                if await more_btn.count() > 0:
                    await more_btn.click()
                    try:
                        await page.wait_for_selector(
                            "div[role='menu']", state="visible", timeout=5000
                        )
                    except Exception:
                        pass
                    await page.wait_for_timeout(500)
                    menu = page.locator("div[role='menu']").first
                    connect_in_menu = menu.locator("text=Connect").first
                    if await connect_in_menu.count() > 0:
                        await connect_in_menu.click()
                    else:
                        # Only Follow/Message available → already connected or not connectable
                        has_message = await scope.locator(
                            "button[aria-label*='Message']"
                        ).count() > 0
                        reason = "already_connected" if has_message else "button_not_found"
                        print(f"Connect not available ({reason}): {profile_url}")
                        result = {"ok": False, "reason": reason}
                        await context.browser.close()
                        return result
                else:
                    has_message = await scope.locator(
                        "button[aria-label*='Message']"
                    ).count() > 0
                    reason = "already_connected" if has_message else "button_not_found"
                    print(f"Connect not available ({reason}): {profile_url}")
                    result = {"ok": False, "reason": reason}
                    await context.browser.close()
                    return result
            else:
                await direct_connect.click()

            # Wait for invite modal
            try:
                await page.wait_for_selector(
                    "div[role='dialog']", state="visible", timeout=8000
                )
            except Exception:
                print("Invite modal did not appear.")
                result = {"ok": False, "reason": "modal_missing"}
                await context.browser.close()
                return result

            dialog = page.locator("div[role='dialog']").first

            if note_text:
                add_note_btn = dialog.locator(
                    "button[aria-label='Add a note'], button[aria-label='Add a free note']"
                ).first
                if await add_note_btn.count() > 0:
                    await add_note_btn.click()
                    await page.wait_for_timeout(800)
                    textarea = dialog.locator(
                        "textarea[name='message'], textarea#custom-message"
                    ).first
                    if await textarea.count() > 0:
                        await textarea.fill(note_text[:300])
                        await page.wait_for_timeout(800)

            send_btn = dialog.locator(
                "button[aria-label='Send invitation'], "
                "button[aria-label='Send without a note'], "
                "button[aria-label='Send now'], "
                "button[aria-label='Send'], "
                "button:has-text('Send')"
            ).first
            if await send_btn.count() == 0:
                print("Send button not found.")
                result = {"ok": False, "reason": "send_not_found"}
            else:
                await send_btn.click()
                await page.wait_for_timeout(2000)

                # Weekly-limit modal?
                limit_modal = page.locator(
                    "div[role='dialog']:has-text('weekly'), "
                    "div[role='dialog']:has-text('reached the weekly')"
                ).first
                if await limit_modal.count() > 0:
                    print(f"Weekly invite limit hit: {profile_url}")
                    result = {"ok": False, "reason": "weekly_limit"}
                else:
                    print(f"Connection request sent: {profile_url}")
                    result = {"ok": True, "reason": "sent"}
        except Exception as e:
            print(f"Failed to connect: {e}")
            result = {"ok": False, "reason": f"exception:{e}"}

        await page.wait_for_timeout(1500)
        await context.browser.close()
        return result


async def repost_post(post_url: str, headless: bool = True) -> dict:
    """Navigate to a post and repost it instantly (no added thoughts)."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        result = {"ok": False, "reason": "unknown"}
        try:
            await page.goto(post_url)
            await page.wait_for_timeout(3000)

            scope = await _find_post_container(page, post_url)
            repost_btn = scope.locator(
                "button[aria-label*='Repost'], button[aria-label*='repost']"
            ).first
            if await repost_btn.count() == 0:
                print("Repost button not found.")
                result = {"ok": False, "reason": "button_not_found"}
            else:
                await repost_btn.click()
                try:
                    await page.wait_for_selector(
                        "div[role='menu']", state="visible", timeout=5000
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(500)

                menu = page.locator("div[role='menu']").first
                # Prefer the instant "Repost" entry, not "Repost with your thoughts"
                confirm = menu.get_by_text(re.compile(r"^\s*Repost\s*$")).first
                if await confirm.count() == 0:
                    confirm = menu.locator(
                        "button:has-text('Repost'), span:has-text('Repost')"
                    ).first

                if await confirm.count() == 0:
                    print("Repost confirm not found.")
                    result = {"ok": False, "reason": "confirm_not_found"}
                else:
                    await confirm.click()
                    await page.wait_for_timeout(2000)
                    print(f"Reposted: {post_url}")
                    result = {"ok": True, "reason": "reposted"}
        except Exception as e:
            print(f"Failed to repost: {e}")
            result = {"ok": False, "reason": f"exception:{e}"}

        await page.wait_for_timeout(1500)
        await context.browser.close()
        return result
