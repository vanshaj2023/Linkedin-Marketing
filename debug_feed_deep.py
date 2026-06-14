"""Deep DOM walker for the new obfuscated /feed/ layout.

Finds post-shaped containers heuristically: any element that has a profile link,
a relative timestamp, AND a Like/Comment/Repost button bar.
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


async def main():
    async with async_playwright() as p:
        ctx = await get_authenticated_context(p, headless=False)
        page = await ctx.new_page()
        await setup_page_stealth(page)
        await safe_sleep(1.0, 2.0)
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        for _ in range(5):
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1500)

        info = await page.evaluate("""() => {
            const profileLinks = [...document.querySelectorAll('a[href*="/in/"], a[href*="/company/"]')];
            const candidates = [];
            const seen = new Set();

            for (const link of profileLinks) {
                let el = link;
                for (let depth = 0; depth < 10; depth++) {
                    el = el.parentElement;
                    if (!el || el.tagName === 'BODY') break;

                    // Score the container
                    const hasLike = !!el.querySelector('button[aria-label*="Like" i], button[aria-label*="React" i]');
                    const hasComment = !!el.querySelector('button[aria-label*="Comment" i]');
                    const hasRepost = !!el.querySelector('button[aria-label*="Repost" i], button[aria-label*="Share" i]');
                    const txt = (el.innerText || '').slice(0, 1000);
                    const hasTimestamp = /\\b\\d+\\s*(s|m|h|d|w|mo|y|hour|day|week|month|year)s?\\b\\s*(ago)?/i.test(txt);

                    if (hasLike && hasComment && hasRepost && txt.length > 80) {
                        if (seen.has(el)) break;
                        seen.add(el);
                        candidates.push({
                            tag: el.tagName,
                            cls: (el.className || '').toString().slice(0, 100),
                            id: el.id || '',
                            text_len: el.innerText.length,
                            text_preview: txt.slice(0, 200).replace(/\\n+/g, ' | '),
                            attrs: [...el.attributes].map(a => `${a.name}=${a.value.slice(0,40)}`).slice(0, 8),
                            child_count: el.children.length,
                            // What's the closest stable anchor near this element?
                            has_data_urn: !!el.querySelector('[data-urn]'),
                            has_data_id: !!el.querySelector('[data-id]'),
                            data_urn_value: el.querySelector('[data-urn]')?.getAttribute('data-urn') || null,
                            data_id_value: el.querySelector('[data-id]')?.getAttribute('data-id') || null,
                        });
                        break;
                    }
                }
            }
            return {
                total_profile_links: profileLinks.length,
                post_candidates: candidates.slice(0, 8),
            };
        }""")

        Path("output").mkdir(exist_ok=True)
        Path("output/feed_deep.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(info, indent=2, ensure_ascii=False)[:4000])
        await ctx.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
