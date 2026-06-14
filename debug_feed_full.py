"""One-shot diagnostic for the /feed/ rendering problem.

Run: python debug_feed_full.py

Saves to output/:
  - feed_debug.png      (screenshot, full page)
  - feed_debug.html     (rendered HTML)
  - feed_debug.json     (counts + URL + title + flags)
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
from browser.manager import (
    get_authenticated_context,
    setup_page_stealth,
    safe_sleep,
)

OUT = Path("output")
OUT.mkdir(exist_ok=True)


async def main(headless: bool = False):
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)
        await safe_sleep(1.0, 2.0)

        print(f"[debug] navigating to /feed/ (headless={headless})...")
        await page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # Try multiple wait strategies before giving up
        wait_results = {}
        try:
            await page.wait_for_selector("main", state="visible", timeout=15000)
            wait_results["main_visible"] = True
        except Exception as e:
            wait_results["main_visible"] = f"fail: {e}"

        try:
            await page.wait_for_selector(
                "[data-urn^='urn:li:activity'], [data-id^='urn:li:activity'], "
                "div.feed-shared-update-v2, div.fie-impression-container, "
                "div.occludable-update",
                timeout=20000,
            )
            wait_results["any_post_selector"] = True
        except Exception as e:
            wait_results["any_post_selector"] = f"fail: {e}"

        await page.wait_for_timeout(3000)

        for i in range(6):
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1200)

        await page.wait_for_timeout(2000)

        info = await page.evaluate("""() => {
            const sel = (s) => document.querySelectorAll(s).length;
            const sample = (s, n=3) => [...document.querySelectorAll(s)].slice(0, n).map(el => ({
                tag: el.tagName,
                cls: (el.className || '').toString().slice(0, 120),
                id: el.id || '',
                attrs: [...el.attributes].slice(0, 6).map(a => `${a.name}="${a.value.slice(0, 60)}"`),
            }));

            const flags = {
                has_main: !!document.querySelector('main'),
                has_feed_container: !!document.querySelector('main .scaffold-finite-scroll, main [role=\"feed\"], main div.feed-identity-module'),
                has_login_form: !!document.querySelector('input#username, input#password'),
                has_captcha: !!document.querySelector('iframe[src*=\"captcha\"], iframe[src*=\"challenge\"], #captcha-internal'),
                has_checkpoint_text: /security verification|please verify|let's do a quick check|unusual activity/i.test(document.body.innerText.slice(0, 5000)),
                has_empty_state: /your feed will appear here|start your feed|no posts yet/i.test(document.body.innerText.slice(0, 5000)),
                body_text_first_500: document.body.innerText.slice(0, 500),
            };

            const counts = {
                'div[data-urn^=\"urn:li:activity\"]': sel('div[data-urn^=\"urn:li:activity\"]'),
                '[data-urn^=\"urn:li:activity\"]': sel('[data-urn^=\"urn:li:activity\"]'),
                'div[data-id^=\"urn:li:activity\"]': sel('div[data-id^=\"urn:li:activity\"]'),
                '[data-id^=\"urn:li:activity\"]': sel('[data-id^=\"urn:li:activity\"]'),
                'div.feed-shared-update-v2': sel('div.feed-shared-update-v2'),
                'div.fie-impression-container': sel('div.fie-impression-container'),
                'div.occludable-update': sel('div.occludable-update'),
                'div[data-finite-scroll-hotkey-item]': sel('div[data-finite-scroll-hotkey-item]'),
                'main .scaffold-finite-scroll__content > div': sel('main .scaffold-finite-scroll__content > div'),
                'main [role=\"article\"]': sel('main [role=\"article\"]'),
                'a[href*=\"urn:li:activity\"]': sel('a[href*=\"urn:li:activity\"]'),
                'main > div': sel('main > div'),
            };

            const samples = {};
            for (const s of Object.keys(counts)) {
                if (counts[s] > 0) samples[s] = sample(s);
            }

            // First 5 direct children of <main> to understand layout
            const mainChildren = [...(document.querySelector('main')?.children || [])].slice(0, 8).map(el => ({
                tag: el.tagName,
                cls: (el.className || '').toString().slice(0, 100),
                id: el.id,
                child_count: el.children.length,
            }));

            return { flags, counts, samples, mainChildren };
        }""")

        result = {
            "url": page.url,
            "title": await page.title(),
            "wait_results": wait_results,
            **info,
        }

        (OUT / "feed_debug.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            await page.screenshot(
                path=str(OUT / "feed_debug.png"),
                full_page=True,
                timeout=10000,
                animations="disabled",
            )
        except Exception as e:
            print(f"[debug] screenshot skipped: {e}")
        (OUT / "feed_debug.html").write_text(await page.content(), encoding="utf-8")

        print(f"\n[debug] saved to output/feed_debug.{{json,png,html}}")
        print(f"[debug] URL: {result['url']}")
        print(f"[debug] Title: {result['title']}")
        print(f"[debug] wait_results: {wait_results}")
        print(f"[debug] flags: {json.dumps(result['flags'], indent=2)}")
        print(f"[debug] counts: {json.dumps(result['counts'], indent=2)}")
        print(f"[debug] mainChildren: {json.dumps(result['mainChildren'], indent=2)}")

        await context.browser.close()


if __name__ == "__main__":
    import sys
    headless = "--headless" in sys.argv
    asyncio.run(main(headless=headless))
