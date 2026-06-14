"""Run _extract_post_data on each candidate and print why it failed."""
import asyncio
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep
from scrapers.feed import (
    _find_post_elements,
    _is_promoted,
    _extract_author_name,
    _extract_content,
    URN_RE,
)


async def main():
    async with async_playwright() as p:
        ctx = await get_authenticated_context(p, headless=False)
        page = await ctx.new_page()
        await setup_page_stealth(page)
        await safe_sleep(1.0, 2.0)
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        for _ in range(6):
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1200)

        posts = await _find_post_elements(page, context="feed")
        print(f"Found {len(posts)} candidates\n")

        for i, el in enumerate(posts[:15]):
            print(f"--- candidate {i} ---")
            try:
                ck = await el.get_attribute("componentkey")
                durn = await el.get_attribute("data-urn")
                did = await el.get_attribute("data-id")
                promoted = await _is_promoted(el)
                author = await _extract_author_name(el)
                content = await _extract_content(el)
                txt_full = await el.inner_text()
                print(f"  componentkey: {ck[:50] if ck else None}")
                print(f"  data-urn: {durn} | data-id: {did}")
                print(f"  is_promoted: {promoted}")
                print(f"  author: {author!r}")
                print(f"  content_len: {len(content)} | preview: {content[:200]!r}")
                print(f"  raw_text_first_300: {txt_full[:300]!r}")
            except Exception as e:
                print(f"  ERROR: {e}")
            print()

        await ctx.browser.close()


asyncio.run(main())
