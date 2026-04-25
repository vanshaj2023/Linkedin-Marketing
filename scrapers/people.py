import asyncio
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


async def scrape_people_search(keyword: str = "Software Engineer", max_results: int = 10, headless: bool = True) -> list:
    """Search LinkedIn for people by keyword and return basic profile metadata."""
    results = []

    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        url = f"https://www.linkedin.com/search/results/people/?keywords={keyword}&origin=CLUSTER_EXPANSION"
        await safe_sleep()
        await page.goto(url)
        await page.wait_for_timeout(5000)

        # Scroll to load results
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight / 3)")
            await page.wait_for_timeout(1000)

        containers = await page.locator("li.reusable-search__result-container").all()

        for container in containers:
            if len(results) >= max_results:
                break
            try:
                link = container.locator("span.entity-result__title-text a.app-aware-link").first
                profile_url = await link.get_attribute("href")
                if profile_url:
                    profile_url = profile_url.split("?")[0]

                name_el = link.locator("span[aria-hidden='true']").first
                name = await name_el.inner_text() if await name_el.count() > 0 else "Unknown"

                headline_el = container.locator("div.entity-result__primary-subtitle").first
                headline = await headline_el.inner_text() if await headline_el.count() > 0 else ""

                location_el = container.locator("div.entity-result__secondary-subtitle").first
                location = await location_el.inner_text() if await location_el.count() > 0 else ""

                if profile_url and "linkedin.com/in/" in profile_url:
                    results.append({
                        "name": name.strip(),
                        "linkedin_url": profile_url,
                        "headline": headline.strip(),
                        "location": location.strip(),
                    })
            except Exception as e:
                print(f"Error scraping people result: {e}")

        await context.browser.close()

    return results


async def search_people(keywords: str, max_results: int = 30) -> list:
    """Agent-facing wrapper. Returns normalised dicts with company field."""
    raw = await scrape_people_search(keyword=keywords, max_results=max_results, headless=True)
    normalised = []
    for r in raw:
        loc = r.get("location", "")
        company = loc.split("\u00b7")[0].strip() if "\u00b7" in loc else loc
        normalised.append({
            "name": r["name"],
            "headline": r.get("headline", ""),
            "company": company,
            "linkedin_url": r["linkedin_url"],
            "mutual_connections": 0,
        })
    return normalised


async def search_company_employees(company: str, max_results: int = 50) -> list:
    """Search for people currently at a specific company."""
    return await search_people(keywords=company, max_results=max_results)


if __name__ == "__main__":
    import json
    results = asyncio.run(scrape_people_search("Software Engineer", 3, headless=False))
    print(json.dumps(results, indent=2))
