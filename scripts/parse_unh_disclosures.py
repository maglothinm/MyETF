import asyncio
import os
from playwright.async_api import async_playwright
import requests

# Pushover credentials from GitHub Secrets
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")

# Terms to scan for
SEARCH_TERMS = ["UNH", "UnitedHealth"]

async def send_pushover_notification(message: str):
    if not PUSHOVER_API_TOKEN or not PUSHOVER_USER_KEY:
        print("Pushover credentials missing.")
        return
    response = requests.post("https://api.pushover.net/1/messages.json", data={
        "token": PUSHOVER_API_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "message": message,
        "title": "📈 UNH Trade Alert",
        "priority": 1
    })
    print("Pushover response:", response.text)

async def scan_senate_disclosures():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("Navigating to Senate Ethics search page...")
        await page.goto("https://efdsearch.senate.gov/search/")
        
        # Accept the checkbox and proceed
        await page.check("#agree_statement")
        await page.click("button[type='submit']")

        # Wait for search form to load
        await page.wait_for_selector("#filing_year")

        # Submit a broad search (e.g. year = 2025)
        await page.select_option("#filing_year", "2025")
        await page.click("input[type='submit']")

        # Wait for results to load
        await page.wait_for_selector("table#filed-reports")

        print("Extracting PDF links...")
        links = await page.locator("table#filed-reports a").all()
        pdf_urls = []
        for link in links:
            href = await link.get_attribute("href")
            if href and href.endswith(".pdf"):
                pdf_urls.append("https://efdsearch.senate.gov" + href)

        print(f"Found {len(pdf_urls)} PDF links.")
        found_matches = []

        for url in pdf_urls:
            try:
                print(f"Scanning: {url}")
                resp = requests.get(url, timeout=20)
                if resp.status_code == 200:
                    content = resp.content.decode("latin1", errors="ignore")
                    for term in SEARCH_TERMS:
                        if term.lower() in content.lower():
                            found_matches.append((term, url))
                            break
            except Exception as e:
                print(f"Failed to scan {url}: {e}")

        await browser.close()

        if found_matches:
            alert_msg = "\n".join([f"{term} found in:\n{url}" for term, url in found_matches])
            await send_pushover_notification(alert_msg)
        else:
            print("No UNH matches found.")

if __name__ == "__main__":
    asyncio.run(scan_senate_disclosures())