"""Inspect what the post's overflow menu actually contains."""
import asyncio
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth
from scrapers.feed import _find_post_elements


async def main():
    async with async_playwright() as p:
        ctx = await get_authenticated_context(p, headless=False)
        page = await ctx.new_page()
        await setup_page_stealth(page)
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1500)

        posts = await _find_post_elements(page, context="feed")
        print(f"Found {len(posts)} posts\n")
        if not posts:
            await ctx.browser.close()
            return

        post = posts[0]
        await post.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)

        # List all buttons in the first post with their aria-labels
        buttons = await post.evaluate("""(el) => {
            return [...el.querySelectorAll('button')].map(b => ({
                aria: b.getAttribute('aria-label') || '',
                text: (b.innerText || '').slice(0, 40),
                visible: !!b.offsetParent,
            })).filter(b => b.aria || b.text);
        }""")
        print("BUTTONS in first post:")
        for b in buttons[:30]:
            print(f"  aria={b['aria']!r:50} text={b['text']!r:30} vis={b['visible']}")

        # Try clicking likely candidates
        candidates = [
            "button[aria-label*='Open control menu' i]",
            "button[aria-label*='More options' i]",
            "button[aria-label*='Open options' i]",
            "button[aria-label='More']",
            "button[aria-label*='control menu' i]",
            "button[aria-label*='options menu' i]",
        ]
        clicked = False
        for sel in candidates:
            btn = post.locator(sel).first
            if await btn.count() > 0:
                print(f"\nFound matching button: {sel}")
                await btn.click()
                clicked = True
                break

        if not clicked:
            # try first button whose aria contains 'menu' or 'more'
            print("\nNo selector matched. Trying JS-based find...")
            await post.evaluate("""(el) => {
                const btns = [...el.querySelectorAll('button')];
                for (const b of btns) {
                    const a = (b.getAttribute('aria-label') || '').toLowerCase();
                    if (a.includes('menu') || a.includes('more') || a.includes('option')) {
                        b.click();
                        console.log('clicked', a);
                        return a;
                    }
                }
                return null;
            }""")

        await page.wait_for_timeout(1500)

        # Dump what opened
        menus = await page.evaluate("""() => {
            const menus = [...document.querySelectorAll('[role="menu"], div.artdeco-dropdown__content--is-open, [class*="dropdown"][class*="open"]')];
            return menus.slice(0, 3).map(m => ({
                tag: m.tagName,
                cls: (m.className || '').toString().slice(0, 100),
                text: (m.innerText || '').slice(0, 500),
                has_urn_text: /urn:li:|urn%3Ali/i.test(m.outerHTML),
                links: [...m.querySelectorAll('a')].slice(0, 5).map(a => ({ href: (a.href || '').slice(0, 120), txt: (a.innerText || '').slice(0,40) })),
                items: [...m.querySelectorAll('[role="menuitem"], button, div[role="button"]')].slice(0, 10).map(b => (b.innerText || '').slice(0, 60)),
            }));
        }""")
        print(f"\nMENUS OPENED ({len(menus)}):")
        import json
        print(json.dumps(menus, indent=2, ensure_ascii=False))

        # Find any element with menu-like keyword
        menu_dump = await page.evaluate(r"""() => {
            const keywords = ['copy link', 'save', 'embed', 'not interested', 'report', 'mute', 'unfollow', 'i don', 'why am i'];
            const all = document.body.querySelectorAll('*');
            const matches = [];
            for (const el of all) {
                if (!el.offsetParent) continue;
                const txt = (el.innerText || '').trim().toLowerCase();
                if (txt.length === 0 || txt.length > 200) continue;
                if (keywords.some(k => txt.includes(k))) {
                    matches.push({
                        tag: el.tagName,
                        cls: (el.className || '').toString().slice(0,80),
                        text: txt.slice(0, 100),
                        html_first_500: el.outerHTML.slice(0, 500),
                    });
                    if (matches.length >= 5) break;
                }
            }
            return matches;
        }""")
        import json
        print("\nMENU-LIKE ELEMENTS:")
        print(json.dumps(menu_dump, indent=2, ensure_ascii=False))

        await page.wait_for_timeout(2000)
        await ctx.browser.close()


asyncio.run(main())
