import asyncio
from urllib.parse import quote_plus
from playwright.async_api import async_playwright
from browser.manager import get_authenticated_context, setup_page_stealth, safe_sleep


async def scrape_recent_jobs(
    keywords: list[str],
    locations: list[str],
    max_per_combo: int = 25,
    easy_apply_only: bool = False,
    headless: bool = True,
) -> list[dict]:
    """Scrape LinkedIn jobs posted in the last 24 hours.

    Returns a list of job dicts with is_easy_apply and external_apply_url fields
    so callers can route to auto-apply vs manual Slack alert.
    """
    results: list[dict] = []
    seen_urls: set[str] = set()

    async with async_playwright() as p:
        context = await get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        await setup_page_stealth(page)

        for keyword in keywords:
            for location in locations:
                combo_results = await _scrape_combo(
                    page, keyword, location, max_per_combo, easy_apply_only
                )
                for job in combo_results:
                    url = job.get("linkedin_post_url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        results.append(job)

        await context.browser.close()

    return results


async def _scrape_combo(
    page, keyword: str, location: str, max_jobs: int, easy_apply_only: bool
) -> list[dict]:
    params = (
        f"keywords={quote_plus(keyword)}"
        f"&location={quote_plus(location)}"
        f"&f_TPR=r86400"
        f"&sortBy=DD"
        f"&origin=JOB_SEARCH_PAGE_SEARCH_BUTTON"
    )
    if easy_apply_only:
        params += "&f_AL=true"

    url = f"https://www.linkedin.com/jobs/search/?{params}"
    await safe_sleep(1.5, 3.0)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[jobs_recent] goto failed for {keyword}/{location}: {e}")
        return []

    try:
        await page.wait_for_selector(
            ".jobs-search-results-list, ul.scaffold-layout__list-container",
            state="visible", timeout=15000,
        )
    except Exception:
        pass

    await page.wait_for_timeout(3000)

    for _ in range(3):
        await page.evaluate("window.scrollBy(0, 600)")
        await page.wait_for_timeout(1000)

    results = []
    job_cards = await page.locator("div.job-card-container, li.scaffold-layout__list-item").all()

    for card in job_cards:
        if len(results) >= max_jobs:
            break
        try:
            await card.click()
            await page.wait_for_timeout(1500)

            title_el = page.locator(
                ".job-details-jobs-unified-top-card__job-title, "
                ".t-24.t-bold, h1.t-24"
            ).first
            title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else ""
            if not title:
                continue

            company_el = page.locator(
                ".job-details-jobs-unified-top-card__company-name a, "
                ".job-details-jobs-unified-top-card__primary-description a"
            ).first
            company = (await company_el.inner_text()).strip() if await company_el.count() > 0 else ""

            link_el = page.locator("a.job-card-list__title, a.job-card-container__link").first
            post_url = await link_el.get_attribute("href") if await link_el.count() > 0 else None
            if post_url:
                post_url = post_url.split("?")[0]
                if post_url.startswith("/jobs/"):
                    post_url = "https://www.linkedin.com" + post_url
            if not post_url:
                continue

            desc_el = page.locator("#job-details, .jobs-description__content").first
            description = (await desc_el.inner_text())[:3000] if await desc_el.count() > 0 else ""

            posted_el = page.locator(
                ".job-details-jobs-unified-top-card__posted-date, "
                "span.tvm__text:has-text('ago')"
            ).first
            posted_ago = (await posted_el.inner_text()).strip() if await posted_el.count() > 0 else ""

            poster_el = page.locator(".hirer-card__hirer-information .app-aware-link").first
            poster_name = ""
            poster_url = None
            if await poster_el.count() > 0:
                poster_name = (await poster_el.inner_text()).strip()
                poster_url = await poster_el.get_attribute("href")
                if poster_url and poster_url.startswith("/in/"):
                    poster_url = "https://www.linkedin.com" + poster_url

            is_easy_apply, external_apply_url = await _detect_apply_type(page)

            results.append({
                "job_title": title,
                "company": company,
                "linkedin_post_url": post_url,
                "description": description,
                "posted_ago_text": posted_ago,
                "poster_name": poster_name,
                "poster_url": poster_url,
                "is_easy_apply": is_easy_apply,
                "external_apply_url": external_apply_url,
            })

        except Exception as e:
            print(f"[jobs_recent] error scraping card: {e}")

    return results


async def _detect_apply_type(page) -> tuple[bool, str | None]:
    """Returns (is_easy_apply, external_apply_url)."""
    easy_btn = page.locator(
        "button.jobs-apply-button:has-text('Easy Apply'), "
        "button[aria-label*='Easy Apply' i]"
    ).first
    if await easy_btn.count() > 0:
        return True, None

    ext_btn = page.locator(
        "button.jobs-apply-button:not(:has-text('Easy Apply')), "
        "a.jobs-apply-button"
    ).first
    if await ext_btn.count() > 0:
        href = await ext_btn.get_attribute("href")
        return False, href

    return False, None


if __name__ == "__main__":
    import json
    jobs = asyncio.run(scrape_recent_jobs(
        keywords=["backend engineer"],
        locations=["India"],
        max_per_combo=5,
        headless=False,
    ))
    print(json.dumps(jobs, indent=2, default=str))
