import asyncio
from playwright.async_api import async_playwright
import browser_manager
import cache
import json

async def scrape_hiring_posts(keyword="hiring", max_posts=3):
    """
    Navigates to LinkedIn content search, extracts the top posts, 
    and returns their raw text, author, and comments (for LLM processing).
    """
    # Ensure cache DB exists
    cache.init_db()
    
    async with async_playwright() as p:
        # headless=False so we can see what it's doing natively in development
        context = await browser_manager.get_authenticated_context(p, headless=False)
        page = await context.new_page()
        
        url = f"https://www.linkedin.com/search/results/content/?keywords={keyword}&origin=GLOBAL_SEARCH_HEADER"
        print(f"Navigating to {url}")
        await page.goto(url)
        
        # Wait for the main feed/search results to load
        # Use a hard sleep to ensure initial React DOM loading completes before checking selectors
        await page.wait_for_timeout(5000)
        
        # Make sure the browser actually focuses on the page so scrolling works safely
        try:
            # Click near the corner to avoid hitting buttons like "Send"
            await page.locator("body").click(position={"x": 10, "y": 10})
        except Exception:
            pass

        print("Scrolling to load posts...")
        for _ in range(4):
            # The most reliable way to trigger infinite scroll in Playwright:
            # 1. Find all currently loaded posts
            current_posts = await page.locator("div[role='listitem']").filter(
                has=page.locator("a[href*='/feed/update/urn:li:activity']")
            ).all()
            
            # 2. Tell the browser to physically scroll down to the very last one
            if current_posts:
                try:
                    await current_posts[-1].scroll_into_view_if_needed()
                except Exception:
                    pass
            
            # 3. Add a small mechanical scroll just to push the boundary 
            await page.evaluate('window.scrollBy(0, 800)')
            
            # 4. Wait for the new batch to fetch via network
            await page.wait_for_timeout(2000)
            
        posts_data = []
        
        # New LinkedIn DOM: Posts are listitems that contain an activity URL
        post_elements = await page.locator("div[role='listitem']").filter(
            has=page.locator("a[href*='/feed/update/urn:li:activity']")
        ).all()
            
        print(f"Found {len(post_elements)} posts on the page.")
        
        # DEBUG MODE: If it still finds 0, save the HTML so we can see exactly what LinkedIn rendered
        if len(post_elements) == 0:
            print("DEBUG: Saving raw HTML to debug_linkedin.html to see what the selectors should be.")
            html_content = await page.content()
            with open("debug_linkedin.html", "w", encoding="utf-8") as f:
                f.write(html_content)
        
        count = 0
        import re
        for post in post_elements:
            if count >= max_posts:
                break
                
            try:
                # Extract URN from the post URL
                post_link = post.locator("a[href*='/feed/update/urn:li:activity']").first
                href = await post_link.get_attribute("href")
                if not href:
                    continue
                match = re.search(r"urn:li:activity:(\d+)", href)
                if not match:
                    continue
                urn = f"urn:li:activity:{match.group(1)}"
                    
                if cache.is_post_processed(urn):
                    print(f"Skipping post {urn} - already processed in DB.")
                    continue
                
                # Extract Author Name and URL
                author_link = post.locator("a[href*='/in/']").first
                author_url = await author_link.get_attribute("href") if await author_link.count() > 0 else ""
                if author_url:
                    author_url = author_url.split('?')[0] # Clean tracking params
                
                author_name = await author_link.inner_text() if await author_link.count() > 0 else "Unknown"
                author_name = author_name.strip().split('\n')[0].strip()
                
                # Extract post content text 
                content_locator = post.locator("[data-testid='expandable-text-box']")
                content_text = await content_locator.inner_text() if await content_locator.count() > 0 else ""
                
                # Extract visible comments if any
                comments_text = ""
                comment_container = post.locator(".comments-comment-item")
                if await comment_container.count() > 0:
                    comments_locator = post.locator(".comments-comments-list")
                    comments_text = await comments_locator.inner_text() if await comments_locator.count() > 0 else ""
                    
                posts_data.append({
                    "post_id": urn,
                    "post_url": f"https://www.linkedin.com/feed/update/{urn}/",
                    "author_name": author_name,
                    "author_url": author_url,
                    "content": content_text,
                    "visible_comments_text": comments_text
                })
                
                count += 1
                
            except Exception as e:
                print(f"Error scraping a post: {e}")
                
        await context.browser.close()
        
        # Write to temporary json for review before LLM Phase
        output_file = "scraped_posts.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(posts_data, f, indent=2)
            
        print(f"\nSuccessfully scraped {len(posts_data)} posts and saved to {output_file}")
        return posts_data

if __name__ == "__main__":
    asyncio.run(scrape_hiring_posts(keyword="hiring", max_posts=3))
