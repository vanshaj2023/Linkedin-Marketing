import asyncio
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


async def scrape_jobs(keyword: str, location: str = "United States", max_jobs: int = 5, headless: bool = True) -> list:
    """Search LinkedIn jobs and scrape titles, companies, and descriptions."""
    results = []

    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={keyword}&location={location}&origin=JOB_SEARCH_PAGE_SEARCH_BUTTON"
        )
        await safe_sleep()
        await page.goto(url)
        await page.wait_for_timeout(5000)

        # Scroll the job list pane
        try:
            await page.locator(".jobs-search-results-list").click()
            for _ in range(3):
                await page.keyboard.press("PageDown")
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        job_cards = await page.locator("div.job-card-container").all()

        for card in job_cards:
            if len(results) >= max_jobs:
                break
            try:
                await card.click()
                await page.wait_for_timeout(1500)

                title_el = page.locator(
                    ".job-details-jobs-unified-top-card__job-title, .t-24.t-bold"
                ).first
                title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else "Unknown Title"

                company_el = page.locator(
                    ".job-details-jobs-unified-top-card__company-name a, "
                    ".job-details-jobs-unified-top-card__primary-description a"
                ).first
                company = (await company_el.inner_text()).strip() if await company_el.count() > 0 else "Unknown"

                link_el = page.locator("a.job-card-list__title, a.job-card-container__link").first
                post_url = await link_el.get_attribute("href")
                if post_url:
                    post_url = post_url.split("?")[0]
                    if post_url.startswith("/jobs/"):
                        post_url = "https://www.linkedin.com" + post_url

                desc_el = page.locator("#job-details, .jobs-description__content").first
                description = (await desc_el.inner_text())[:3000] if await desc_el.count() > 0 else ""

                poster_el = page.locator(".hirer-card__hirer-information span").first
                poster = (await poster_el.inner_text()).strip() if await poster_el.count() > 0 else ""

                if post_url:
                    results.append({
                        "job_title": title,
                        "company": company,
                        "linkedin_post_url": post_url,
                        "description": description,
                        "poster_name": poster,
                        "poster_text": "",
                    })
            except Exception as e:
                print(f"Error scraping job: {e}")

        await context.browser.close()

    return results


async def search_jobs(keywords: str, location: str = "", max_results: int = 20) -> list:
    """Agent-facing wrapper."""
    loc = location if location else "Remote"
    return await scrape_jobs(keyword=keywords, location=loc, max_jobs=max_results, headless=True)


if __name__ == "__main__":
    jobs = asyncio.run(scrape_jobs("Python Developer", "Remote", 2, headless=False))
    print(jobs)
