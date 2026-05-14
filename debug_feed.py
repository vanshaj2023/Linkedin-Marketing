import asyncio
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


async def debug():
    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=False)
        page = await context.new_page()
        await setup_page_stealth(page)
        await safe_sleep()
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)

        vp = page.viewport_size or {"width": 1280, "height": 720}
        feed_x = vp["width"] * 0.38
        feed_y = vp["height"] * 0.5
        await page.mouse.click(feed_x, feed_y)
        await page.wait_for_timeout(800)
        for _ in range(8):
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1500)

        # Find ALL elements that contain an activity link and print their tag + classes
        info = await page.evaluate("""() => {
            const links = [...document.querySelectorAll('a[href*="/feed/update/urn:li:activity"]')];
            const seen = new Set();
            const results = [];
            for (const a of links) {
                // Walk up to find the post container (5 levels max)
                let el = a;
                for (let i = 0; i < 5; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    const key = el.tagName + '|' + el.className.slice(0, 60);
                    if (!seen.has(key)) {
                        seen.add(key);
                        results.push({
                            tag: el.tagName,
                            cls: el.className.slice(0, 80),
                            level: i + 1,
                            href: a.href.slice(0, 80)
                        });
                    }
                }
                if (results.length >= 20) break;
            }
            return results;
        }""")

        print(f"Found {len(info)} parent elements containing activity links:\n")
        for r in info:
            print(f"  Level {r['level']} | {r['tag']} | {r['cls']}")
            print(f"          href: {r['href']}\n")

        await context.browser.close()


asyncio.run(debug())
