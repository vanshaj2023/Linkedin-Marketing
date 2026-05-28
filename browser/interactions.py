import re
import random
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


URN_RE = re.compile(r"urn:li:[a-zA-Z]+:\d+")


class ActionFailedError(Exception):
    """Raised when a browser interaction fails in a way the queue should retry."""
    pass


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
            await safe_sleep(1.0, 3.0)
            await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            scope = await _find_post_container(page, post_url)

            # Already-liked detection
            unlike_btn = scope.locator("button[aria-label*='Unlike']").first
            if await unlike_btn.count() > 0:
                result = {"ok": True, "reason": "already_liked"}
                print(f"Already liked: {post_url}")
            else:
                # Try multiple selector strategies for Like button
                like_btn = None
                like_selectors = [
                    "button[aria-label='React Like']",
                    "button[aria-label='Like']",
                    "button[aria-label*='like' i]",
                ]
                for sel in like_selectors:
                    loc = scope.locator(sel).first
                    if await loc.count() > 0:
                        like_btn = loc
                        break

                # Fallback: find by role
                if not like_btn:
                    loc = scope.get_by_role("button", name=re.compile(r"like", re.IGNORECASE)).first
                    if await loc.count() > 0:
                        like_btn = loc

                if not like_btn or await like_btn.count() == 0:
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

        await page.wait_for_timeout(random.randint(1000, 2000))
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
            await safe_sleep(1.0, 3.0)
            await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            scope = await _find_post_container(page, post_url)

            # The comment form may not be inside `scope` (it's often a sibling);
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

        await page.wait_for_timeout(random.randint(1000, 2000))
        await context.browser.close()
        return result


async def send_connection_request(
    profile_url: str, note_text: str = None, headless: bool = True
) -> dict:
    """Navigate to a profile and send a connection request with optional note.

    Returns: {"ok": bool, "reason": str}
        reason in {"sent", "pending", "already_connected", "button_not_found",
                  "weekly_limit", "send_failed", "modal_missing", "exception:..."}

    Raises ActionFailedError for retryable failures so the queue processor
    can mark the action as failed and retry it.
    """
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        result = {"ok": False, "reason": "unknown"}
        try:
            await safe_sleep(1.5, 4.0)
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector("main", state="visible", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(random.randint(2000, 4000))

            # Scope to profile top card
            top = page.locator(
                "section.pv-top-card, section[data-member-id], "
                "section.artdeco-card, main"
            ).first
            scope = top if await top.count() > 0 else page

            # ── State detection ──────────────────────────────────────────────

            # Check if already pending
            pending_btn = scope.locator("button[aria-label*='Pending']").first
            if await pending_btn.count() == 0:
                pending_btn = scope.get_by_role("button", name=re.compile(r"pending", re.IGNORECASE)).first
            if await pending_btn.count() > 0:
                print(f"Already pending: {profile_url}")
                result = {"ok": False, "reason": "pending"}
                await context.browser.close()
                return result

            # ── Find Connect button ──────────────────────────────────────────
            connect_clicked = False

            # Strategy 1: Direct Connect button with multiple aria-label patterns
            connect_selectors = [
                "button[aria-label='Connect']",
                "button[aria-label*='connect' i]",
                "button[aria-label*='Invite'][aria-label*='connect' i]",
                "button[aria-label*='Connect'][aria-label*='invite' i]",
            ]
            for sel in connect_selectors:
                btn = scope.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    connect_clicked = True
                    break

            # Strategy 2: Use getByRole with flexible name matching
            if not connect_clicked:
                role_btn = scope.get_by_role(
                    "button", name=re.compile(r"connect", re.IGNORECASE)
                ).first
                if await role_btn.count() > 0:
                    btn_text = (await role_btn.inner_text()).strip().lower()
                    # Avoid clicking "Message" or "Follow" buttons
                    if "connect" in btn_text:
                        await role_btn.click()
                        connect_clicked = True

            # Strategy 3: Try the "More" overflow menu
            if not connect_clicked:
                more_btn = scope.locator(
                    "button[aria-label='More actions'], button[aria-label='More']"
                ).first
                if await more_btn.count() == 0:
                    more_btn = scope.get_by_role(
                        "button", name=re.compile(r"more", re.IGNORECASE)
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
                    if await menu.count() > 0:
                        # Look for Connect in the dropdown menu
                        connect_in_menu = menu.get_by_role(
                            "menuitem", name=re.compile(r"connect", re.IGNORECASE)
                        ).first
                        if await connect_in_menu.count() == 0:
                            connect_in_menu = menu.locator(
                                "span:has-text('Connect'), div:has-text('Connect')"
                            ).first

                        if await connect_in_menu.count() > 0:
                            await connect_in_menu.click()
                            connect_clicked = True
                        else:
                            # Close the menu by pressing Escape
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(300)

            if not connect_clicked:
                # Determine if already connected or truly not available
                has_message = await scope.locator(
                    "button[aria-label*='Message']"
                ).count() > 0
                reason = "already_connected" if has_message else "button_not_found"
                print(f"Connect not available ({reason}): {profile_url}")
                result = {"ok": False, "reason": reason}
                if reason == "button_not_found":
                    raise ActionFailedError(f"Connect button not found: {profile_url}")
                await context.browser.close()
                return result

            # ── Wait for invite modal ────────────────────────────────────────
            await page.wait_for_timeout(random.randint(800, 1500))
            try:
                await page.wait_for_selector(
                    "div[role='dialog']", state="visible", timeout=8000
                )
            except Exception:
                # Sometimes LinkedIn sends the request directly without a modal
                # Check if the button changed to "Pending"
                pending_check = scope.locator("button[aria-label*='Pending']").first
                if await pending_check.count() > 0:
                    print(f"Connection sent (no modal): {profile_url}")
                    result = {"ok": True, "reason": "sent"}
                    await context.browser.close()
                    return result
                print("Invite modal did not appear.")
                result = {"ok": False, "reason": "modal_missing"}
                raise ActionFailedError(f"Invite modal did not appear: {profile_url}")

            dialog = page.locator("div[role='dialog']").first

            # ── Add note (if provided) ───────────────────────────────────────
            if note_text:
                # Try multiple selectors for "Add a note" button
                add_note_btn = None
                add_note_selectors = [
                    "button[aria-label='Add a note']",
                    "button[aria-label='Add a free note']",
                    "button:has-text('Add a note')",
                ]
                for sel in add_note_selectors:
                    btn = dialog.locator(sel).first
                    if await btn.count() > 0:
                        add_note_btn = btn
                        break

                if not add_note_btn:
                    add_note_btn = dialog.get_by_role(
                        "button", name=re.compile(r"add a note", re.IGNORECASE)
                    ).first

                if add_note_btn and await add_note_btn.count() > 0:
                    await add_note_btn.click()
                    await page.wait_for_timeout(random.randint(600, 1200))

                    # Find the textarea
                    textarea = dialog.locator(
                        "textarea[name='message'], "
                        "textarea#custom-message, "
                        "textarea"
                    ).first
                    if await textarea.count() > 0:
                        # Type with human-like delays
                        await textarea.click()
                        await page.keyboard.type(
                            note_text[:300],
                            delay=random.randint(30, 70),
                        )
                        await page.wait_for_timeout(random.randint(500, 1000))

            # ── Click Send ───────────────────────────────────────────────────
            send_btn = None
            send_selectors = [
                "button[aria-label='Send invitation']",
                "button[aria-label='Send without a note']",
                "button[aria-label='Send now']",
                "button[aria-label='Send']",
            ]
            for sel in send_selectors:
                btn = dialog.locator(sel).first
                if await btn.count() > 0:
                    send_btn = btn
                    break

            # Fallback: getByRole
            if not send_btn or await send_btn.count() == 0:
                send_btn = dialog.get_by_role(
                    "button", name=re.compile(r"^send", re.IGNORECASE)
                ).first

            # Fallback: text-based
            if not send_btn or await send_btn.count() == 0:
                send_btn = dialog.locator("button:has-text('Send')").first

            if not send_btn or await send_btn.count() == 0:
                print("Send button not found in modal.")
                result = {"ok": False, "reason": "send_not_found"}
                raise ActionFailedError(f"Send button not found: {profile_url}")

            await send_btn.click()
            await page.wait_for_timeout(random.randint(2000, 3500))

            # ── Check for weekly limit modal ─────────────────────────────────
            limit_modal = page.locator(
                "div[role='dialog']:has-text('weekly'), "
                "div[role='dialog']:has-text('reached the weekly'), "
                "div[role='dialog']:has-text('invitation limit')"
            ).first
            if await limit_modal.count() > 0:
                print(f"Weekly invite limit hit: {profile_url}")
                result = {"ok": False, "reason": "weekly_limit"}
                raise ActionFailedError("Weekly connection request limit reached")
            else:
                print(f"Connection request sent: {profile_url}")
                result = {"ok": True, "reason": "sent"}

        except ActionFailedError:
            await page.wait_for_timeout(random.randint(1000, 2000))
            await context.browser.close()
            raise
        except Exception as e:
            print(f"Failed to connect: {e}")
            result = {"ok": False, "reason": f"exception:{e}"}

        await page.wait_for_timeout(random.randint(1000, 2000))
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
            await safe_sleep(1.0, 3.0)
            await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            scope = await _find_post_container(page, post_url)
            repost_btn = scope.locator(
                "button[aria-label*='Repost'], button[aria-label*='repost']"
            ).first
            if await repost_btn.count() == 0:
                repost_btn = scope.get_by_role(
                    "button", name=re.compile(r"repost", re.IGNORECASE)
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

        await page.wait_for_timeout(random.randint(1000, 2000))
        await context.browser.close()
        return result
