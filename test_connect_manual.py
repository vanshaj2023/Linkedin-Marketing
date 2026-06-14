import asyncio
from browser.interactions import send_connection_request

TEST_URL = "https://www.linkedin.com/in/kumar-vanshaj-435450293/"

async def main():
    result = await send_connection_request(
        profile_url=TEST_URL,
        note_text="Hey, saw your work — would love to connect.",
        headless=False,
    )
    print("Result:", result)

asyncio.run(main())
