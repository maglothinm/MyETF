import os
import re
import asyncio
from pushover import Client
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# CONFIG
KEYWORDS = ["UNH", "UnitedHealth", "United Health Group"]
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
LOG_FILE = "senate_unh_matches.log"

# Helper: Send Pushover alert
def send_pushover_alert(title, message):
    try:
        client = Client(user_key=PUSHOVER_USER_KEY, api_token=PUSHOVER_API_TOKEN)
        client.send_message(message, title=title)
    except Exception as e:
        print(f"❌ Failed to send Pushover alert: {e}")

# Main Playwright logic
async def main():
    url = "https://www.ethics.senate.gov/public/index.cfm/financial-disclosure-reports"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("🌐 Navigating to Senate Ethics search page...")
        await page.goto(url, timeout=60000)

        # Try multiple selectors in case the page structure changed
        try:
            print("⏳ Waiting for Senate page to load...")
            await page.wait_for_selector("select#filing_year, select[name='report_year']", timeout=90000)
            print("✅ Found year selector.")
        except PlaywrightTimeoutError:
            print("❌ Could not find filing year selector on Senate Ethics page.")
            print("⚠️ The site structure may have changed. Consider switching to ZIP-based parsing instead.")
            await browser.close()
            return

        # Example scraping logic (modify this section as needed for real use)
        content = await page.content()

        # Search page content for keywords
        for keyword in KEYWORDS:
            if keyword.lower() in content.lower():
                print(f"✅ Match found: {keyword}")
                message = f"UNH-related term found on Senate Ethics page:\n\nKeyword: {keyword}\n\n{url}"
                send_pushover_alert("Senate UNH Disclosure Detected", message)
                with open(LOG_FILE, "a") as f:
                    f.write(f"{keyword} | {url}\n")
                break
        else:
            print("🔍 No UNH-related terms found on the page.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())