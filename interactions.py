import asyncio
from playwright.async_api import async_playwright
import browser_manager

async def react_to_post(post_url: str):
    """
    Navigates to a specific post URL and clicks the 'Like' button.
    """
    async with async_playwright() as p:
        context = await browser_manager.get_authenticated_context(p, headless=False)
        page = await context.new_page()
        
        print(f"\nNavigating to post to React: {post_url}")
        await page.goto(post_url)
        
        # Give the post time to load
        await page.wait_for_timeout(3000)
        
        try:
            # LinkedIn "Like" buttons usually have a specific aria-label or classes
            # They change frequently, but aria-label="Like" or aria-label="React Like" is usually consistent.
            # Look for button that says "Like"
            like_button_locator = page.locator("button[aria-label='React Like'], button[aria-label='Like']").first
            
            if await like_button_locator.count() > 0:
                print("Found Like button. Clicking it...")
                await like_button_locator.click()
                print("Success: Reacted to post.")
            else:
                print("Could not find the Like button.")
        except Exception as e:
            print(f"Failed to react: {e}")
            
        await page.wait_for_timeout(2000)
        await context.browser.close()

async def comment_on_post(post_url: str, comment_text: str, headless: bool = True):
    """
    Navigates to a specific post URL, types a comment, and posts it.
    """
    async with async_playwright() as p:
        context = await browser_manager.get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        
        print(f"\nNavigating to post to Comment: {post_url}")
        await page.goto(post_url)
        
        await page.wait_for_timeout(3000)
        
        try:
            # Find the comment box. It uses a specific editor class or aria-label 'Add a comment...'
            comment_box = page.locator("div[role='textbox'][aria-label*='comment'], div.ql-editor").first
            
            if await comment_box.count() > 0:
                print("Found comment box. Typing comment...")
                await comment_box.click()
                
                # Type human-like
                await page.keyboard.type(comment_text, delay=50)
                await page.wait_for_timeout(1000)
                
                # Click the submit/Post button
                import re
                
                # The actual submit button is a primary button (blue) that says "Comment" or "Post".
                # The main comment action toggle on the post is a ghost/secondary button, so we filter by primary.
                submit_button = page.locator("button.artdeco-button--primary:has-text('Comment'), button.artdeco-button--primary:has-text('Post')").first
                
                # Check if it was found
                if await submit_button.count() > 0:
                    await submit_button.click()
                    print("Success: Comment posted.")
                else:
                    print("Could not find the Post button in the UI.")
                    with open("debug_comment.html", "w", encoding="utf-8") as f:
                        f.write(await page.content())
                    print("DEBUG: Saved raw HTML to debug_comment.html")
            else:
                print("Could not find the comment entry box.")
        except Exception as e:
            print(f"Failed to comment: {e}")
            
        await page.wait_for_timeout(2000)
        await context.browser.close()

async def send_connection_request(profile_url: str, note_text: str = None, headless: bool = True):
    """
    Navigates to a LinkedIn profile and sends a connection request with an optional note.
    """
    async with async_playwright() as p:
        context = await browser_manager.get_authenticated_context(p, headless=headless)
        page = await context.new_page()
        
        print(f"\nNavigating to profile to Connect: {profile_url}")
        await page.goto(profile_url)
        
        await page.wait_for_timeout(3000)
        
        try:
            import re
            main_container = page.locator("main").first
            
            # Use Playwright's strict accessible naming to beat the obfuscated DOM.
            # Look for either a button explicitly named "Connect" or with the aria-label "Invite <name> to connect".
            connect_btn = main_container.get_by_role("button", name=re.compile(r"^(Connect|Invite .* to connect)$", re.IGNORECASE)).first
            
            try:
                # Wait up to 8 seconds for the Connect button to dynamically render
                await connect_btn.wait_for(state="visible", timeout=8000)
            except Exception:
                pass # It's okay if it times out, it might be in the 'More' menu or not available
                
            if await connect_btn.count() == 0:
                print("Connect button not instantly visible. Checking 'More' menu in profile...")
                # The "More actions" button in the profile header
                more_btn = main_container.get_by_role("button", name=re.compile(r"More", re.IGNORECASE)).first
                if await more_btn.count() > 0:
                    await more_btn.click()
                    await page.wait_for_timeout(1000)
                    # The dropdown menu is usually attached to the body or main, not necessarily inside top_section
                    # Look for the exact menu item that says Connect
                    connect_btn = page.locator("div[role='dialog'], div[role='menu']").get_by_role("menuitem", name=re.compile(r"Connect", re.IGNORECASE)).first
                    if await connect_btn.count() == 0:
                        # Fallback for weird dropdowns
                        connect_btn = page.locator("div[role='dialog'], div[role='menu']").locator("div, span").filter(has_text=re.compile(r"^Connect$")).first

            if await connect_btn.count() > 0:
                print("Found Connect button. Clicking...")
                await connect_btn.click()
                await page.wait_for_timeout(2000)
                
                # Now a modal usually pops up asking to add a note or send.
                if note_text:
                    add_note_btn = page.locator("button[aria-label='Add a note']").first
                    if await add_note_btn.count() > 0:
                        await add_note_btn.click()
                        await page.wait_for_timeout(500)
                        
                        textarea = page.locator("textarea[name='message']").first
                        await textarea.fill(note_text)
                        await page.wait_for_timeout(1000)
                
                # Check if we can find any send button
                send_btn = page.locator("button[aria-label='Send without a note'], button[aria-label='Send now'], button[aria-label='Send'], button:has-text('Send')").last
                
                # Try to save HTML for debugging
                with open("debug_connect.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
                print("DEBUG: Saved raw HTML to debug_connect.html")
                
                await send_btn.click()
                print("Success: Connection request sent.")
            else:
                print("Could not find a way to connect to this user.")
        except Exception as e:
            print(f"Failed to connect: {e}")
            try:
                with open("debug_connect_error.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
                print("DEBUG: Saved raw HTML to debug_connect_error.html")
            except:
                pass
            
        await page.wait_for_timeout(2000)
        await context.browser.close()

async def repost_post(post_url: str):
    """
    Navigates to a specific post URL and clicks the Repost button to repost without additional text.
    """
    async with async_playwright() as p:
        context = await browser_manager.get_authenticated_context(p, headless=True)
        page = await context.new_page()

        print(f"\nNavigating to post to Repost: {post_url}")
        await page.goto(post_url)
        await page.wait_for_timeout(3000)

        try:
            repost_btn = page.locator("button[aria-label*='Repost'], button[aria-label*='repost']").first
            if await repost_btn.count() > 0:
                await repost_btn.click()
                await page.wait_for_timeout(1000)
                # Confirm plain "Repost" in the dropdown (not "Repost with your thoughts")
                confirm_btn = page.locator("div[role='menu'] span:has-text('Repost'), button:has-text('Repost')").first
                if await confirm_btn.count() > 0:
                    await confirm_btn.click()
                    print("Success: Reposted.")
                else:
                    print("Could not find Repost confirm button in dropdown.")
            else:
                print("Could not find the Repost button.")
        except Exception as e:
            print(f"Failed to repost: {e}")

        await page.wait_for_timeout(2000)
        await context.browser.close()


if __name__ == "__main__":
    import sys
    # For testing: pass a post URL and test reaction
    # python interactions.py react "https://www.linkedin.com/feed/update/urn:li:activity:XXX"
    if len(sys.argv) > 2:
        action = sys.argv[1]
        url = sys.argv[2]
        
        if action == "react":
            asyncio.run(react_to_post(url))
        elif action == "comment" and len(sys.argv) > 3:
            asyncio.run(comment_on_post(url, sys.argv[3]))
        elif action == "connect":
            asyncio.run(send_connection_request(url, sys.argv[3] if len(sys.argv) > 3 else None))
