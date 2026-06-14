import re
import random
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep
from browser.vision_fallback import vision_assisted_connect_click, fast_screenshot


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


_FIND_TOPCARD_BUTTON_JS = r"""
({kind}) => {
  // kind: 'connect' or 'more'
  // Tags the chosen element with data-claude-target=<marker> so Playwright
  // can click via locator. Returns rich diagnostic info on every candidate.

  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const REC_PATTERNS = [
    /more profiles for you/i,
    /people you may know/i,
    /people also viewed/i,
    /other similar profiles/i,
    /you might like/i,
    /suggested for you/i,
    /promoted/i,
  ];

  // Walk ancestors looking for right-rail markers.
  // We do NOT exclude <li> blindly — LinkedIn's top-card action row often
  // uses <ul><li> for Connect/Message/Follow. Only exclude when inside an
  // aside / complementary container.
  const railReason = (el) => {
    let a = el.parentElement;
    while (a && a !== document.body) {
      const tag = a.tagName;
      const role = (a.getAttribute && a.getAttribute('role')) || '';
      const aria = (a.getAttribute && a.getAttribute('aria-label')) || '';
      if (tag === 'ASIDE') return 'aside';
      if (role === 'complementary') return 'complementary';
      if (REC_PATTERNS.some((p) => p.test(aria))) return 'aria:' + aria.slice(0, 40);
      a = a.parentElement;
    }
    return null;
  };

  const isConnect = (text, aria) => {
    const t = (text || '').toLowerCase();
    const a = (aria || '').toLowerCase();
    if (/(^|\s)connect(\s|$)/.test(t) && t.length < 40) return true;
    if (/^invite\b.*\bto connect\b/.test(a)) return true;
    if (a === 'connect') return true;
    if (/^connect\b/.test(a) && a.length < 40) return true;
    return false;
  };

  const isMore = (text, aria) => {
    const t = (text || '').toLowerCase();
    const a = (aria || '').toLowerCase();
    if (t === 'more' || t === '…') return true;
    if (a === 'more actions' || a === 'more') return true;
    if (/^more\b/.test(a) && a.length < 40) return true;
    return false;
  };

  // Cast a wider net: <button>, <a>, anything role="button".
  const allEls = Array.from(
    document.querySelectorAll("button, [role='button'], a")
  );

  const allCandidates = [];
  const chosen_pool = [];

  for (const el of allEls) {
    const text = (el.textContent || '').trim();
    const aria = (el.getAttribute('aria-label') || '').trim();
    const match = (kind === 'connect') ? isConnect(text, aria) : isMore(text, aria);
    if (!match) continue;

    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0;
    const rail = railReason(el);
    const rightX = rect.x > vpW * 0.65;

    const record = {
      tag: el.tagName,
      text: text.slice(0, 50),
      aria: aria.slice(0, 80),
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      w: Math.round(rect.width),
      h: Math.round(rect.height),
      visible,
      rail,
      rightX,
    };
    allCandidates.push(record);

    if (!visible) continue;
    if (rail) continue;
    if (rightX) continue;
    chosen_pool.push({ el, x: rect.x, y: rect.y, aria, text, record });
  }

  // Pick topmost remaining candidate.
  chosen_pool.sort((a, b) => a.y - b.y);

  if (chosen_pool.length === 0) {
    return {
      found: false,
      marker: null,
      allCandidates,
      viewport: { w: vpW, h: vpH },
    };
  }

  const chosen = chosen_pool[0];
  const marker = 'cct-' + Math.random().toString(36).slice(2, 10);
  chosen.el.setAttribute('data-claude-target', marker);
  try {
    chosen.el.scrollIntoView({ block: 'center', inline: 'center' });
  } catch (e) {}

  return {
    found: true,
    marker,
    chosen: chosen.record,
    allCandidates,
    viewport: { w: vpW, h: vpH },
  };
}
"""


async def _find_topcard_button(page, kind: str) -> dict:
    """Find the main profile's Connect (or More) button via DOM structural rules.

    Tags the chosen button with data-claude-target=<marker> so the caller can
    click it via `page.locator("button[data-claude-target='<marker>']")`.
    """
    return await page.evaluate(_FIND_TOPCARD_BUTTON_JS, {"kind": kind})


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
                  "weekly_limit", "send_failed", "modal_missing", "wrong_profile",
                  "exception:..."}

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
            try:
                await page.goto(profile_url, wait_until="commit", timeout=60000)
            except Exception as nav_err:
                print(f"[connect] goto(commit) failed: {nav_err}; retrying with 'load'")
                await page.goto(profile_url, wait_until="load", timeout=60000)
            try:
                await page.wait_for_selector("main", state="visible", timeout=20000)
            except Exception:
                pass
            # Wait for the top-card action row (Connect / Message / Follow / More)
            # so we don't probe the DOM before React has hydrated the profile.
            try:
                await page.wait_for_selector(
                    "main button[aria-label*='Connect' i], "
                    "main button[aria-label*='Message' i], "
                    "main button[aria-label*='Follow' i], "
                    "main button[aria-label*='More actions' i], "
                    "main button[aria-label*='Pending' i]",
                    state="visible",
                    timeout=15000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(random.randint(2000, 4000))

            # ── Own-profile short-circuit ────────────────────────────────────
            # You cannot connect to yourself; LinkedIn shows Edit / Resources
            # instead of Connect. Detect this before scoring "button not found".
            own_profile_signals = [
                "button[aria-label*='Edit your profile' i]",
                "a[href*='/me/edit/topcard']",
                "button:has-text('Open to')",
                "button:has-text('Add profile section')",
                "button:has-text('Resources')",
            ]
            for sel in own_profile_signals:
                if await page.locator(sel).first.count() > 0:
                    print(f"Cannot connect to your own profile: {profile_url}")
                    result = {"ok": False, "reason": "own_profile"}
                    await context.browser.close()
                    return result

            # ── Anchor on the profile owner's name element ───────────────────
            # LinkedIn ships a few different DOM variants for the top card.
            # We probe a handful of known selectors instead of assuming `main h1`.
            profile_name = ""
            h1 = None
            name_selectors = [
                "main h1",
                "h1.text-heading-xlarge",
                "section.pv-top-card h1",
                "section[data-view-name='profile-card'] h1",
                ".pv-text-details__left-panel h1",
                ".ph5 h1",
                "[data-anonymize='person-name']",
                "main [class*='top-card'] h1",
                "h1",
            ]
            for sel in name_selectors:
                el = page.locator(sel).first
                if await el.count() > 0:
                    try:
                        txt = (await el.inner_text()).strip()
                    except Exception:
                        txt = ""
                    if txt:
                        profile_name = txt
                        h1 = el
                        break

            # JS-based fallback: extract name from document.title only. We
            # deliberately do NOT scan the page for Invite-aria-label buttons
            # here because the first such button in the DOM is often a
            # right-rail recommendation, not the profile owner.
            if not profile_name:
                try:
                    title = await page.title()
                    m = re.match(r"^([^|]+)\s*\|", title or "")
                    if m:
                        cand = m.group(1).strip()
                        # LinkedIn's auth/error pages don't have a name format
                        if cand and cand.lower() not in ("linkedin", "feed"):
                            profile_name = cand
                            print(f"[connect] resolved name via title: {profile_name!r}")
                except Exception as e:
                    print(f"[connect] title name fallback failed: {e}")

            if not profile_name:
                try:
                    title = await page.title()
                except Exception:
                    title = "<unavailable>"
                print(
                    f"[connect] name element not found on {profile_url}; "
                    f"page title={title!r} — falling through to vision."
                )
                try:
                    import os
                    os.makedirs("output/connect_vision", exist_ok=True)
                    img = await fast_screenshot(page, full_page=True)
                    with open("output/connect_vision/no_name_debug.png", "wb") as f:
                        f.write(img)
                    print(
                        "[connect] saved diagnostic screenshot to "
                        "output/connect_vision/no_name_debug.png"
                    )
                except Exception as e:
                    print(f"[connect] could not save diagnostic screenshot: {e}")

            # ── Resolve strict top-card scope ────────────────────────────────
            top = None
            if h1 is not None and await h1.count() > 0:
                anchored = h1.locator("xpath=ancestor::section[1]")
                if await anchored.count() > 0:
                    top = anchored.first

            if top is None:
                legacy = page.locator(
                    "section.pv-top-card, section[data-view-name='profile-card'], "
                    "section[data-member-id]"
                ).first
                if await legacy.count() > 0:
                    top = legacy

            if top is None:
                # Last resort: first section inside main — never `main` itself,
                # which would sweep in "People you may know" / "People also viewed".
                top = page.locator("main > section").first

            scope = top

            # ── State detection ──────────────────────────────────────────────

            # Already pending (must be in the top card, not a recommendation).
            pending_btn = scope.locator("button[aria-label*='Pending' i]").first
            if await pending_btn.count() == 0:
                pending_btn = scope.get_by_role(
                    "button", name=re.compile(r"pending", re.IGNORECASE)
                ).first
            if await pending_btn.count() > 0:
                print(f"Already pending: {profile_url}")
                result = {"ok": False, "reason": "pending"}
                await context.browser.close()
                return result

            # ── Find Connect button (strict scope) ───────────────────────────
            connect_clicked = False

            # Strategy 0: DOM-structure-based search. Walks every button on
            # the page, excludes those in <aside> / role=complementary /
            # recommendation containers, then picks the topmost remaining
            # match in the left ~65% of the viewport.
            try:
                topcard = await _find_topcard_button(page, "connect")
            except Exception as e:
                print(f"[connect] _find_topcard_button(connect) error: {e}")
                topcard = {"found": False}

            if topcard.get("found"):
                chosen = topcard.get("chosen", {})
                print(
                    f"[connect] structural Connect pick: tag={chosen.get('tag')}, "
                    f"aria={chosen.get('aria')!r}, text={chosen.get('text')!r}, "
                    f"pos=({chosen.get('x')},{chosen.get('y')})"
                )
                if not profile_name:
                    aria = chosen.get("aria") or ""
                    m = re.match(r"Invite\s+(.+?)\s+to connect", aria, re.IGNORECASE)
                    if m:
                        profile_name = m.group(1).strip()
                        print(f"[connect] resolved name from button: {profile_name!r}")
                marker = topcard["marker"]
                btn = page.locator(
                    f"[data-claude-target='{marker}']"
                ).first
                try:
                    await btn.click()
                    connect_clicked = True
                except Exception as e:
                    print(f"[connect] structural Connect click failed: {e}")
            else:
                all_cands = topcard.get("allCandidates", [])
                vp = topcard.get("viewport", {})
                print(
                    f"[connect] structural Connect search found 0 top-card matches "
                    f"out of {len(all_cands)} Connect-like elements (viewport={vp})"
                )
                for c in all_cands[:12]:
                    print(f"   • {c}")
                # Save a diagnostic screenshot so we can see the actual page state.
                try:
                    import os
                    os.makedirs("output/connect_vision", exist_ok=True)
                    img = await fast_screenshot(page, full_page=False)
                    with open(
                        "output/connect_vision/structural_miss.png", "wb"
                    ) as f:
                        f.write(img)
                    print(
                        "[connect] saved diagnostic screenshot to "
                        "output/connect_vision/structural_miss.png"
                    )
                except Exception as e:
                    print(f"[connect] could not save diagnostic screenshot: {e}")

                # Strategy 0a: fall back to structural More-button search and
                # then look for "Connect" in the resulting menu.
                try:
                    more_res = await _find_topcard_button(page, "more")
                except Exception as e:
                    print(f"[connect] _find_topcard_button(more) error: {e}")
                    more_res = {"found": False}
                if more_res.get("found"):
                    mchosen = more_res.get("chosen", {})
                    print(
                        f"[connect] structural More pick: tag={mchosen.get('tag')}, "
                        f"aria={mchosen.get('aria')!r}, text={mchosen.get('text')!r}, "
                        f"pos=({mchosen.get('x')},{mchosen.get('y')})"
                    )
                    more_btn = page.locator(
                        f"[data-claude-target='{more_res['marker']}']"
                    ).first
                    try:
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
                            exact_connect = re.compile(
                                r"^\s*Connect\s*$", re.IGNORECASE
                            )
                            connect_in_menu = menu.locator(
                                "div[role='button'], div[role='menuitem'], "
                                "li button, button"
                            ).filter(has_text=exact_connect).first
                            if await connect_in_menu.count() > 0:
                                await connect_in_menu.click()
                                connect_clicked = True
                                print(
                                    "[connect] clicked Connect inside More menu"
                                )
                            else:
                                print("[connect] no Connect item inside More menu")
                                await page.keyboard.press("Escape")
                    except Exception as e:
                        print(f"[connect] structural More click failed: {e}")

            # Strategy 0b: name-anchored aria-label — strongest identity check.
            # LinkedIn's Connect button typically reads "Invite <Name> to connect".
            if not connect_clicked and profile_name:
                safe_name = profile_name.replace("'", "").replace('"', "")
                name_anchored = scope.locator(
                    f"button[aria-label*=\"{safe_name}\"][aria-label*='connect' i]"
                ).first
                if await name_anchored.count() > 0:
                    await name_anchored.click()
                    connect_clicked = True

            # Strategy 1: any Connect button strictly inside the top card.
            if not connect_clicked:
                connect_selectors = [
                    "button[aria-label='Connect']",
                    "button[aria-label*='Invite'][aria-label*='connect' i]",
                    "button[aria-label*='connect' i]:not([aria-label*='Pending' i])",
                ]
                for sel in connect_selectors:
                    btn = scope.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click()
                        connect_clicked = True
                        break

            # Strategy 2: getByRole, but require exact "Connect" name match.
            if not connect_clicked:
                role_btn = scope.get_by_role(
                    "button", name=re.compile(r"^connect$", re.IGNORECASE)
                ).first
                if await role_btn.count() > 0:
                    await role_btn.click()
                    connect_clicked = True

            # Strategy 3: open the top-card "More" overflow menu.
            if not connect_clicked:
                more_btn = scope.locator(
                    "button[aria-label='More actions'], button[aria-label='More']"
                ).first
                if await more_btn.count() == 0:
                    more_btn = scope.get_by_role(
                        "button", name=re.compile(r"^more", re.IGNORECASE)
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
                        exact_connect = re.compile(r"^\s*Connect\s*$", re.IGNORECASE)
                        connect_in_menu = menu.locator(
                            "div[role='button'], div[role='menuitem'], li button"
                        ).filter(has_text=exact_connect).first
                        if await connect_in_menu.count() == 0:
                            connect_in_menu = menu.get_by_role(
                                "menuitem", name=exact_connect
                            ).first
                        if await connect_in_menu.count() == 0:
                            connect_in_menu = menu.get_by_role(
                                "button", name=exact_connect
                            ).first

                        if await connect_in_menu.count() > 0:
                            await connect_in_menu.click()
                            connect_clicked = True
                        else:
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(300)

            # Strategy 4 (backup): vision-LLM fallback. Only fires when the
            # deterministic strategies failed to locate Connect inside the
            # strict top-card scope.
            if not connect_clicked:
                has_message = await scope.locator(
                    "button[aria-label*='Message' i]"
                ).count() > 0
                follow_btn = scope.locator(
                    "button[aria-label*='Follow' i]"
                    ":not([aria-label*='Following' i])"
                    ":not([aria-label*='Unfollow' i])"
                ).first
                has_follow = await follow_btn.count() > 0

                # If only Message is exposed (no Follow), the user is already
                # connected — no point burning a vision call on that.
                if has_message and not has_follow:
                    print(f"Connect not available (already_connected): {profile_url}")
                    result = {"ok": False, "reason": "already_connected"}
                    await context.browser.close()
                    return result

                print(
                    f"[connect] Deterministic strategies failed for {profile_url}; "
                    f"trying vision fallback."
                )
                vision_ok, vision_reason = await vision_assisted_connect_click(
                    page, profile_name, save_debug_dir="output/connect_vision"
                )
                if vision_ok:
                    connect_clicked = True
                    print(f"[connect] vision fallback succeeded: {vision_reason}")
                else:
                    reason = "follow_only_profile" if has_follow else "button_not_found"
                    print(
                        f"Connect not available ({reason}, vision={vision_reason}): "
                        f"{profile_url}"
                    )
                    result = {"ok": False, "reason": reason}
                    if reason == "button_not_found":
                        raise ActionFailedError(
                            f"Connect button not found (vision={vision_reason}): "
                            f"{profile_url}"
                        )
                    await context.browser.close()
                    return result

            # ── Wait for invite modal ────────────────────────────────────────
            await page.wait_for_timeout(random.randint(800, 1500))
            try:
                await page.wait_for_selector(
                    "div[role='dialog'], div.artdeco-modal, "
                    "div[data-test-modal-id*='send-invite']",
                    state="visible",
                    timeout=8000,
                )
            except Exception:
                pending_check = scope.locator("button[aria-label*='Pending' i]").first
                if await pending_check.count() > 0:
                    print(f"Connection sent (no modal): {profile_url}")
                    result = {"ok": True, "reason": "sent"}
                    await context.browser.close()
                    return result
                print("Invite modal did not appear.")
                result = {"ok": False, "reason": "modal_missing"}
                raise ActionFailedError(f"Invite modal did not appear: {profile_url}")

            dialog = page.locator(
                "div[role='dialog']:not([id^='artdeco-hoverable-msg_overlay'])"
                ":not([id^='artdeco-modal-outlet--ad-banner'])"
            ).filter(
                has=page.locator(
                    "button:has-text('Send'), button:has-text('Add a note'), "
                    "button[aria-label*='Send invitation' i]"
                )
            ).first

            # ── Verify modal recipient matches the profile owner ─────────────
            # Safety net: even if a Connect click slipped through to the wrong
            # card, the invite modal will name the recipient. If their first
            # name doesn't appear anywhere in the modal text, abort.
            if profile_name and await dialog.count() > 0:
                try:
                    dialog_text = await dialog.inner_text()
                except Exception:
                    dialog_text = ""
                first_token = profile_name.split()[0] if profile_name else ""
                if first_token and first_token.lower() not in dialog_text.lower():
                    print(
                        f"[connect] Modal recipient mismatch — aborting "
                        f"(expected '{profile_name}', modal head='{dialog_text[:120]}')"
                    )
                    cancel = dialog.locator(
                        "button[aria-label*='Dismiss' i], button[aria-label*='Cancel' i]"
                    ).first
                    if await cancel.count() > 0:
                        try:
                            await cancel.click()
                        except Exception:
                            pass
                    else:
                        await page.keyboard.press("Escape")
                    result = {"ok": False, "reason": "wrong_profile"}
                    raise ActionFailedError(
                        f"Modal recipient mismatch for {profile_url}"
                    )

            # ── Add note (if provided) ───────────────────────────────────────
            if note_text:
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

                    textarea = dialog.locator(
                        "textarea[name='message'], "
                        "textarea#custom-message, "
                        "textarea"
                    ).first
                    if await textarea.count() > 0:
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

            if not send_btn or await send_btn.count() == 0:
                send_btn = dialog.get_by_role(
                    "button", name=re.compile(r"^send", re.IGNORECASE)
                ).first

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


async def send_direct_message(
    profile_url: str, message_text: str, headless: bool = True
) -> dict:
    """Open the profile, click Message, type, send.

    Returns {"ok": bool, "reason": str} where reason is one of:
        sent | message_button_not_found | compose_not_found |
        input_not_found | send_not_found | exception:...
    """
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        result = {"ok": False, "reason": "unknown"}
        try:
            await safe_sleep(1.5, 3.5)
            await page.goto(profile_url, wait_until="commit", timeout=60000)
            try:
                await page.wait_for_selector(
                    "main button[aria-label*='Message' i]",
                    state="visible", timeout=15000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(random.randint(1500, 3000))

            # Top-card Message button only — skip right-rail "Message" links to
            # recommended profiles. Scope to the first <section> in <main>.
            top = page.locator("main > section").first
            msg_btn = top.locator(
                "button[aria-label*='Message' i]:not([aria-label*='Pending' i])"
            ).first
            if await msg_btn.count() == 0:
                msg_btn = page.get_by_role(
                    "button", name=re.compile(r"^message", re.IGNORECASE)
                ).first

            if await msg_btn.count() == 0:
                await context.browser.close()
                return {"ok": False, "reason": "message_button_not_found"}

            await msg_btn.click()
            await page.wait_for_timeout(random.randint(1200, 2200))

            # Compose surface — LinkedIn shows either an overlay bubble or a
            # full-screen dialog depending on viewport / experiments.
            compose = page.locator(
                "div.msg-overlay-conversation-bubble, "
                "div[role='dialog']:has(div.msg-form__contenteditable), "
                "div.msg-form"
            ).first
            try:
                await compose.wait_for(state="visible", timeout=8000)
            except Exception:
                await context.browser.close()
                return {"ok": False, "reason": "compose_not_found"}

            input_box = compose.locator(
                "div.msg-form__contenteditable[contenteditable='true'], "
                "div[contenteditable='true'][role='textbox']"
            ).first
            if await input_box.count() == 0:
                await context.browser.close()
                return {"ok": False, "reason": "input_not_found"}

            await input_box.click()
            await page.keyboard.type(
                message_text[:1900], delay=random.randint(25, 60),
            )
            await page.wait_for_timeout(random.randint(800, 1400))

            send_btn = compose.locator(
                "button.msg-form__send-button:not([disabled]), "
                "button[type='submit']:has-text('Send')"
            ).first
            if await send_btn.count() == 0:
                send_btn = compose.get_by_role(
                    "button", name=re.compile(r"^send$", re.IGNORECASE)
                ).first

            if await send_btn.count() == 0:
                await context.browser.close()
                return {"ok": False, "reason": "send_not_found"}

            await send_btn.click()
            await page.wait_for_timeout(random.randint(1800, 2800))
            print(f"DM sent: {profile_url}")
            result = {"ok": True, "reason": "sent"}

        except Exception as e:
            print(f"Failed to DM: {e}")
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
