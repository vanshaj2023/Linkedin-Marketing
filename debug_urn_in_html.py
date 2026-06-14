import asyncio
import re
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth


async def go():
    async with async_playwright() as p:
        ctx = await get_authenticated_context(p, headless=False)
        page = await ctx.new_page()
        await setup_page_stealth(page)
        await page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(10000)
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1500)

        html = await page.content()
        matches = re.findall(
            r"urn(?::|%3A)li(?::|%3A)(activity|share|ugcPost)(?::|%3A)(\d+)",
            html,
            re.IGNORECASE,
        )
        print(f"URN matches in HTML: {len(matches)}")
        print(f"First 5: {matches[:5]}")
        print(f"HTML size: {len(html)}")

        # Also check window state / scripts
        urns_in_scripts = await page.evaluate(
            """() => {
            const re = /urn(?::|%3A)li(?::|%3A)(activity|share|ugcPost)(?::|%3A)(\\d+)/gi;
            const out = new Set();
            for (const s of document.querySelectorAll('script')) {
                const matches = (s.textContent || '').matchAll(re);
                for (const m of matches) out.add(m[0]);
            }
            return [...out].slice(0, 10);
        }"""
        )
        print(f"URNs in <script> tags: {len(urns_in_scripts)}: {urns_in_scripts[:5]}")

        await ctx.browser.close()


asyncio.run(go())
