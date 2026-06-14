import re
import asyncio
import random
from playwright.async_api import async_playwright
from browser.manager import (
    get_authenticated_context,
    setup_page_stealth,
    safe_sleep,
    validate_session,
)


URN_RE = re.compile(r"urn:li:(activity|share|ugcPost):(\d+)")

# Selector used to identify any element containing a LinkedIn post URN link.
_URN_LINK_SEL = (
    "a[href*='urn:li:activity'], "
    "a[href*='urn:li:share'], "
    "a[href*='urn:li:ugcPost']"
)


async def _is_promoted(post_element) -> bool:
    try:
        return await post_element.locator(
            "span:has-text('Promoted'), span:has-text('Sponsored'), "
            ":text-is('Promoted'), :text-is('Sponsored')"
        ).count() > 0
    except Exception:
        return False


async def _extract_author_name(post_element) -> str:
    """Extract the author name. Walks up the DOM in JS to find the actor block
    that may live in an ancestor of the data-urn container."""
    try:
        name = await post_element.evaluate("""(el) => {
            const pickFromLink = (link) => {
                if (!link) return null;
                const aria = link.getAttribute('aria-label');
                if (aria) {
                    const m = aria.match(/(?:View\\s+)?(.+?)(?:['\\u2019]s\\s+(?:profile|page|graphic link)|\\s*•|$)/i);
                    if (m && m[1]) {
                        const n = m[1].trim();
                        if (n && !['view','profile','follow'].includes(n.toLowerCase())) return n;
                    }
                }
                const txt = (link.innerText || '').trim();
                if (txt) {
                    for (const raw of txt.split('\\n')) {
                        const t = raw.trim();
                        if (!t) continue;
                        if (t.startsWith('•')) continue;
                        const low = t.toLowerCase();
                        if (['follow','following','+ follow','connect','message'].includes(low)) continue;
                        return t;
                    }
                }
                return null;
            };
            let scope = el;
            for (let i = 0; i < 6; i++) {
                if (!scope) break;
                const span = scope.querySelector(
                    'span.update-components-actor__title span[aria-hidden="true"], ' +
                    'span[class*="actor__title"] span[aria-hidden="true"], ' +
                    'span[class*="actor__name"] span[aria-hidden="true"]'
                );
                if (span) {
                    const t = (span.innerText || '').trim().split('\\n')[0].trim();
                    if (t) return t;
                }
                for (const sel of ['a[href*="/in/"]', 'a[href*="/company/"]', 'a[href*="/school/"]']) {
                    const links = scope.querySelectorAll(sel);
                    for (const link of links) {
                        const n = pickFromLink(link);
                        if (n) return n;
                    }
                }
                scope = scope.parentElement;
            }
            return null;
        }""")
        if name:
            return name
    except Exception:
        pass

    # Strategy 1: Visible name inside the actor span
    selectors = [
        "span.update-components-actor__name span[aria-hidden='true']",
        "span.update-components-actor__name",
        "span.feed-shared-actor__name span[aria-hidden='true']",
        "span.feed-shared-actor__name",
        ".update-components-actor__title span[aria-hidden='true']",
        "span[class*='actor__name']",
    ]
    for sel in selectors:
        el = post_element.locator(sel).first
        if await el.count() > 0:
            try:
                raw = (await el.inner_text()).strip()
                if raw:
                    # LinkedIn duplicates name for a11y: "Jane\nJane\n• 1st\n• 1d"
                    for line in raw.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("•"):
                            return line
            except Exception:
                continue

    # Strategy 2: Profile link text
    author_link = post_element.locator("a[href*='/in/']").first
    if await author_link.count() > 0:
        try:
            txt = (await author_link.inner_text()).strip()
            if txt:
                return txt.split("\n")[0].strip()
        except Exception:
            pass

    return "Unknown"


async def _expand_see_more(post_element) -> None:
    """Click the inline 'see more' toggle so the full post body is in the DOM.

    Uses JS evaluate to find any visible button whose text ends in 'more' (excluding
    'View N more comments' etc). This survives class-name changes.
    """
    try:
        await post_element.evaluate("""(el) => {
            const isVisible = (node) => !!(node && node.offsetParent !== null);
            const buttons = el.querySelectorAll('button, span[role="button"]');
            for (const b of buttons) {
                if (!isVisible(b)) continue;
                const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (!t) continue;
                if (t.includes('comment') || t.includes('repl')) continue;
                if (/^\\s*[…\\.]{1,3}\\s*more\\s*$/i.test(t) || t === 'see more' || t === '…more' || t === '… more') {
                    b.click();
                    return true;
                }
            }
            return false;
        }""")
        await post_element.page.wait_for_timeout(300)
    except Exception:
        pass


async def _extract_content(post_element) -> str:
    """Extract the text content of a feed post."""
    await _expand_see_more(post_element)
    selectors = [
        "[data-testid='expandable-text-box']",
        "[data-testid*='post-text']",
        "[data-testid*='commentary']",
        ".update-components-text",
        ".feed-shared-update-v2__description",
        ".feed-shared-inline-show-more-text",
        "div[class*='update-components-text']",
    ]
    for sel in selectors:
        el = post_element.locator(sel).first
        if await el.count() > 0:
            try:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
            except Exception:
                continue

    # New-layout fallback: strip chrome (author/timestamp/reactions/buttons) from the
    # post's innerText and return what remains.
    try:
        body = await post_element.evaluate("""(el) => {
            const clone = el.cloneNode(true);
            const drop = clone.querySelectorAll(
                'button, header, footer, [role="button"], time, svg, ' +
                'a[href*="/in/"], a[href*="/company/"], a[href*="/school/"], ' +
                '[aria-label*="reaction" i], [aria-label*="comment" i], ' +
                '[aria-label*="repost" i], [aria-label*="like" i], ' +
                '[data-testid*="footer" i], [data-testid*="actions" i], ' +
                '[data-testid*="header" i], [data-testid*="meta" i]'
            );
            drop.forEach(n => n.remove());
            return (clone.innerText || '').trim();
        }""")
        if body:
            lines = [l.strip() for l in body.split("\n") if l.strip()]
            chrome_exact = {
                "feed post", "follow", "following", "+ follow", "promoted",
                "sponsored", "recommended for you", "see more", "see less",
                "like", "comment", "repost", "send", "more", "view profile",
                "view all", "show more results", "edited",
            }
            # Patterns: connection degree, timestamps, "N followers"
            import_re = __import__('re')
            patterns = [
                import_re.compile(r"^[•·]?\s*\d+(st|nd|rd\+?|st\+?|nd\+?)$", import_re.IGNORECASE),
                import_re.compile(r"^[•·]?\s*\d+(s|m|h|d|w|mo|y)\s*[•·]?\s*$", import_re.IGNORECASE),
                import_re.compile(r"^\d[\d,]*\s+followers?$", import_re.IGNORECASE),
                import_re.compile(r"^[•·]\s*$"),
            ]
            cleaned = []
            for l in lines:
                low = l.lower().strip()
                if low in chrome_exact:
                    continue
                if any(p.match(l) for p in patterns):
                    continue
                cleaned.append(l)
            if cleaned:
                # Drop leading short lines (author/headline) — keep from first long line onward
                start_idx = next(
                    (i for i, x in enumerate(cleaned) if len(x) >= 60),
                    0,
                )
                return "\n".join(cleaned[start_idx:])
    except Exception:
        pass
    return ""


async def _extract_post_data(post_element) -> dict | None:
    """Extract structured data from a single feed post element."""
    try:
        if await _is_promoted(post_element):
            return None

        urn = None
        data_urn = await post_element.get_attribute("data-urn")
        if data_urn:
            m = URN_RE.search(data_urn)
            if m:
                urn = f"urn:li:{m.group(1)}:{m.group(2)}"

        if not urn:
            data_id = await post_element.get_attribute("data-id")
            if data_id:
                m = URN_RE.search(data_id)
                if m:
                    urn = f"urn:li:{m.group(1)}:{m.group(2)}"

        if not urn:
            post_link = post_element.locator(_URN_LINK_SEL).first
            if await post_link.count() > 0:
                href = await post_link.get_attribute("href") or ""
                match = URN_RE.search(href)
                if match:
                    urn = f"urn:li:{match.group(1)}:{match.group(2)}"

        # New layout fallback: componentkey as the session-stable pseudo-ID
        component_key = None
        if not urn:
            component_key = await post_element.get_attribute("componentkey")
            if component_key:
                urn = f"componentkey:{component_key}"

        if not urn:
            return None

        author_name = await _extract_author_name(post_element)
        content = await _extract_content(post_element)
        if not content:
            return None

        # Extract the author's profile URL from the first /in/ link in the actor block.
        author_url: str | None = None
        try:
            author_url = await post_element.evaluate("""(el) => {
                const link = el.querySelector(
                    'a[href*="/in/"], a[href*="/company/"]'
                );
                if (!link) return null;
                const href = link.getAttribute('href') || '';
                const m = href.match(/\\/(?:in|company)\\/[^/?#]+/);
                return m ? 'https://www.linkedin.com' + m[0] : null;
            }""")
        except Exception:
            pass

        post_url = (
            f"https://www.linkedin.com/feed/update/{urn}/"
            if urn.startswith("urn:li:")
            else ""  # new-layout posts: no permalink without clicking through
        )
        return {
            "post_id": urn,
            "post_url": post_url,
            "author_name": author_name,
            "author_url": author_url,
            "content": content,
        }
    except Exception:
        return None


def _post_container_selectors(context: str = "feed") -> str:
    """Return the CSS selectors for post containers based on page context.

    LinkedIn uses different container structures on different pages:
    - Feed: posts are in <li> elements or div.feed-shared-update-v2
    - Search results: posts are in div[role='listitem']
    - Activity page: posts are in <li> elements
    """
    if context == "search":
        return "div[role='listitem']"
    return "div[data-urn^='urn:li:activity'], div[data-urn^='urn:li:share'], div[data-urn^='urn:li:ugcPost']"


async def _find_post_elements(page, context: str = "feed") -> list:
    """Find all post elements on the current page.

    Primary strategy: anchor on data-urn, the most stable attribute LinkedIn exposes.
    Fallback: scan generic listitems containing a URN link (search results pages).
    """
    # New (2026) React build: stamp each found post with data-scraper-post-id so we can
    # return Playwright Locators (which have .locator()) instead of ElementHandles.
    # mainFeed also contains the post composer / sort selector / "people you may know"
    # widgets — filter to only componentkey blocks with a Like+Comment action bar.
    count = await page.evaluate("""() => {
        document.querySelectorAll('[data-scraper-post-id]').forEach(el => el.removeAttribute('data-scraper-post-id'));
        const main = document.querySelector('[data-testid="mainFeed"]');
        if (!main) return 0;
        const all = [...main.querySelectorAll('[componentkey]')];
        const outermost = all.filter(el => {
            let p = el.parentElement;
            while (p && p !== main) {
                if (p.hasAttribute('componentkey')) return false;
                p = p.parentElement;
            }
            return true;
        });
        const isRealPost = (el) => {
            const hasLike = !!el.querySelector('button[aria-label*="Like" i], button[aria-label*="React" i]');
            const hasComment = !!el.querySelector('button[aria-label*="Comment" i]');
            const hasShare = !!el.querySelector('button[aria-label*="Repost" i], button[aria-label*="Share" i], button[aria-label*="Send" i]');
            const txt = (el.innerText || '');
            return hasLike && hasComment && hasShare && txt.length > 80;
        };
        const posts = outermost.filter(isRealPost);
        posts.forEach((el, i) => el.setAttribute('data-scraper-post-id', String(i)));
        return posts.length;
    }""")
    if count > 0:
        return [page.locator(f'[data-scraper-post-id="{i}"]') for i in range(count)]

    primary = await page.locator(
        "[data-urn^='urn:li:activity'], "
        "[data-urn^='urn:li:share'], "
        "[data-urn^='urn:li:ugcPost'], "
        "[data-id^='urn:li:activity'], "
        "[data-id^='urn:li:share'], "
        "[data-id^='urn:li:ugcPost']"
    ).all()
    if primary:
        return primary

    fallback = await page.locator(
        "div.feed-shared-update-v2, "
        "div.fie-impression-container, "
        "li.scaffold-finite-scroll__content > div, "
        "div[role='listitem']"
    ).filter(has=page.locator(_URN_LINK_SEL)).all()
    if fallback:
        return fallback

    # Last resort: structural walk — stamp posts with data-scraper-post-id and return Locators.
    walk_count = await page.evaluate("""() => {
        document.querySelectorAll('[data-scraper-post-id]').forEach(el => el.removeAttribute('data-scraper-post-id'));
        const out = [];
        const seen = new Set();
        const urnLinks = document.querySelectorAll(
            'a[href*="urn:li:activity"], a[href*="urn:li:share"], a[href*="urn:li:ugcPost"]'
        );
        for (const a of urnLinks) {
            const wrap = a.closest('article, [data-urn], [data-id], li, div.fie-impression-container, div.feed-shared-update-v2') || a.parentElement;
            if (wrap && !seen.has(wrap)) { seen.add(wrap); out.push(wrap); }
        }
        if (out.length === 0) {
            const profileLinks = document.querySelectorAll('a[href*="/in/"], a[href*="/company/"]');
            const raw = [];
            for (const link of profileLinks) {
                let el = link;
                for (let depth = 0; depth < 10; depth++) {
                    el = el.parentElement;
                    if (!el || el.tagName === 'BODY') break;
                    if (seen.has(el)) break;
                    const hasLike = !!el.querySelector('button[aria-label*="Like" i], button[aria-label*="React" i]');
                    const hasComment = !!el.querySelector('button[aria-label*="Comment" i]');
                    const hasShare = !!el.querySelector('button[aria-label*="Repost" i], button[aria-label*="Share" i], button[aria-label*="Send" i]');
                    const txt = (el.innerText || '');
                    if (hasLike && hasComment && hasShare && txt.length > 80) {
                        seen.add(el);
                        raw.push(el);
                        break;
                    }
                }
            }
            const innermost = raw.filter(el => !raw.some(other => other !== el && el.contains(other)));
            out.push(...innermost);
        }
        out.forEach((el, i) => el.setAttribute('data-scraper-post-id', String(i)));
        return out.length;
    }""")
    return [page.locator(f'[data-scraper-post-id="{i}"]') for i in range(walk_count)]


async def scrape_hiring_posts(
    keyword: str = "hiring", max_posts: int = 3, headless: bool = True
) -> list:
    """Search LinkedIn content by keyword and return matching posts."""
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        url = (
            f"https://www.linkedin.com/search/results/content/"
            f"?keywords={keyword}&origin=GLOBAL_SEARCH_HEADER"
        )
        await safe_sleep()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        # Validate session after navigation
        if not await validate_session(page):
            print("Session invalid — redirected to login.")
            await context.browser.close()
            return []

        for _ in range(4):
            posts = await _find_post_elements(page, context="search")
            if posts:
                try:
                    await posts[-1].scroll_into_view_if_needed()
                except Exception:
                    pass
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(2000)

        post_elements = await _find_post_elements(page, context="search")

        results = []
        for el in post_elements:
            if len(results) >= max_posts:
                break
            data = await _extract_post_data(el)
            if data:
                results.append(data)

        await context.browser.close()
        return results


async def _recover_urn_via_menu(post_locator) -> str | None:
    """Click the post's '...' menu and pull the URN from the share link.

    Used on the 2026 obfuscated feed where URNs are stripped from initial DOM.
    The menu opens as a popover mounted outside the post; we find it by locating
    the element containing 'Copy link' text, then grep its ancestors for the URN.
    Returns urn:li:... or None.
    """
    page = post_locator.page
    try:
        more_btn = post_locator.locator(
            "button[aria-label*='Open control menu' i], "
            "button[aria-label*='control menu' i], "
            "button[aria-label*='More options' i], "
            "button[aria-label*='Open options' i], "
            "button[aria-label='More']"
        ).first
        if await more_btn.count() == 0:
            return None
        await more_btn.scroll_into_view_if_needed()
        await more_btn.click(timeout=3000)
        await page.wait_for_timeout(700)

        urn = await page.evaluate(r"""() => {
            const re = /urn(?::|%3A)li(?::|%3A)(activity|share|ugcPost)(?::|%3A)(\d+)/i;
            // Find any visible element whose text contains a menu item we know
            // appears in the post control menu: "Copy link", "Save", "Embed",
            // "Not interested", "Report post".
            const itemKeywords = /copy link|embed this post|not interested|report post|^save$|^unsave$/i;
            const all = document.body.querySelectorAll('*');
            for (const el of all) {
                if (!el.offsetParent) continue;
                const txt = (el.innerText || '').trim();
                if (txt.length === 0 || txt.length > 300) continue;
                if (!itemKeywords.test(txt)) continue;
                // Walk up to a menu/popover container, looking for URN in its DOM
                let cur = el;
                for (let i = 0; i < 8; i++) {
                    if (!cur) break;
                    const m = (cur.outerHTML || '').match(re);
                    if (m) return `urn:li:${m[1].toLowerCase()}:${m[2]}`;
                    cur = cur.parentElement;
                }
            }
            return null;
        }""")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        return urn
    except Exception:
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return None


async def scrape_organic_feed(
    max_posts: int = 5, headless: bool = True, enrich_urns: bool = True
) -> list:
    """Scroll the main feed and extract organic posts.

    enrich_urns: if True, recover real urn:li:activity IDs for new-layout posts by
                 clicking each post's '...' menu. Slower but yields real post_url.
                 Set False to keep componentkey:* pseudo-IDs (faster).
    """
    results = []
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        # Capture URNs from Voyager feed responses (post URN no longer exists in DOM
        # on the obfuscated 2026 layout — only available in GraphQL payload).
        captured_urns: list[str] = []
        seen_urns: set[str] = set()

        voyager_urls_seen: list[str] = []
        # Matches both raw "urn:li:activity:123" and URL-encoded "urn%3Ali%3Aactivity%3A123"
        urn_any_re = re.compile(
            r"urn(?::|%3A)li(?::|%3A)(activity|share|ugcPost)(?::|%3A)(\d+)",
            re.IGNORECASE,
        )

        async def _capture_urns(response):
            url = response.url
            if "/voyager/api/" not in url and "/graphql" not in url:
                return
            # Only feed/update endpoints — skip notifications, identity, etc.
            lowered = url.lower()
            if not any(k in lowered for k in ("feed", "update", "main-feed", "mainfeed")):
                return
            try:
                body = await response.text()
            except Exception:
                return
            matches = urn_any_re.findall(body)
            voyager_urls_seen.append(f"[{len(matches)}u {len(body)}b] {url[:100]}")
            for kind, oid in matches:
                urn = f"urn:li:{kind.lower()}:{oid}"
                if urn not in seen_urns:
                    seen_urns.add(urn)
                    captured_urns.append(urn)

        page.on("response", _capture_urns)

        await safe_sleep()
        await page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        if not await validate_session(page):
            print("Session invalid — redirected to login.")
            await context.browser.close()
            return []

        try:
            await page.wait_for_selector("main", state="attached", timeout=15000)
        except Exception:
            print("[feed] <main> never appeared")

        await page.wait_for_timeout(8000)
        for _ in range(8):
            await page.evaluate("window.scrollBy(0, 900)")
            await page.wait_for_timeout(1500)

        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text())[:5000]
        except Exception:
            pass
        if any(s in body_text.lower() for s in (
            "security verification", "let's do a quick check",
            "please verify", "unusual activity", "we want to make sure",
        )):
            print("[feed] interstitial / checkpoint detected on /feed/")
            await context.browser.close()
            return []

        post_elements = await _find_post_elements(page, context="feed")
        print(f"[feed] found {len(post_elements)} candidate containers at {page.url}")

        # Extract data alongside its source locator so we can enrich URNs later.
        extracted: list[tuple[dict, object]] = []
        for el in post_elements:
            if len(extracted) >= max_posts:
                break
            data = await _extract_post_data(el)
            if data:
                extracted.append((data, el))
        results = [d for d, _ in extracted]

        # Backfill real URNs:
        #  1. Voyager network capture (rare on new layout)
        #  2. Rendered HTML scan (SSR'd state — also rare on new layout)
        #  3. Per-post '...' menu click (slow but reliable)
        if not captured_urns:
            try:
                html = await page.content()
                for kind, oid in urn_any_re.findall(html):
                    urn = f"urn:li:{kind.lower()}:{oid}"
                    if urn not in seen_urns:
                        seen_urns.add(urn)
                        captured_urns.append(urn)
            except Exception:
                pass

        urn_iter = iter(captured_urns)
        for data, el in extracted:
            if not (data["post_id"].startswith("componentkey:") and data["post_url"] == ""):
                continue
            real_urn = next(urn_iter, None)
            if not real_urn and enrich_urns:
                real_urn = await _recover_urn_via_menu(el)
                if real_urn:
                    await page.wait_for_timeout(random.randint(400, 900))
            if real_urn:
                data["post_id"] = real_urn
                data["post_url"] = f"https://www.linkedin.com/feed/update/{real_urn}/"

        print(f"[feed] extracted {len(results)} usable posts | captured {len(captured_urns)} URNs from Voyager")
        if voyager_urls_seen:
            # Sort: URLs that had URN matches first, then by body size desc
            with_urns = [u for u in voyager_urls_seen if not u.startswith("[0u ")]
            without_urns = [u for u in voyager_urls_seen if u.startswith("[0u ")]
            print(f"[feed] voyager URLs WITH URNs ({len(with_urns)}): {with_urns[:5]}")
            if not with_urns:
                # show the biggest 5 bodies to find where post data might live
                without_urns.sort(key=lambda s: -int(s.split("u ")[1].split("b")[0]) if "u " in s else 0)
                print(f"[feed] biggest voyager bodies (no URNs): {without_urns[:5]}")
        await context.browser.close()
    return results


async def scrape_user_latest_post(
    profile_url: str, headless: bool = True
) -> dict | None:
    """Fetch the most recent post from a user's activity page."""
    activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        await safe_sleep()
        await page.goto(
            activity_url, wait_until="domcontentloaded", timeout=60000
        )
        await page.wait_for_timeout(4000)

        post_elements = await _find_post_elements(page, context="feed")
        result = None
        if post_elements:
            result = await _extract_post_data(post_elements[0])

        await context.browser.close()
    return result


if __name__ == "__main__":
    posts = asyncio.run(scrape_hiring_posts("hiring", 3, headless=False))
    print(posts)
