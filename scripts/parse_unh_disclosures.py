import os
import asyncio
import subprocess
from playwright.async_api import async_playwright
import re

# Ensure Playwright and Chromium browser are installed (for GitHub Actions)
subprocess.run(["playwright", "install", "chromium"], check=True)

# UNH-related search terms
SEARCH_TERMS = ["UNH", "UnitedHealth"]

# Pushover credentials from GitHub Secrets
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")


async def scan_senate_disclosures():
    print("Navigating to Senate Ethics search page...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Go to the Senate disclosure search page
        await page.goto("https://efdsearch.senate.gov/search/")
        await page.wait_for_load_state("domcontentloaded")

        # Screenshot for debugging purposes (saved as artifact)
        await page.screenshot(path="debug.png", full_page=True)

        try:
            # Wait for the year dropdown selector to load
            await page.wait_for_selector("#filing_year", timeout=60000)
        except Exception as e:
            print(f"Error: Selector #filing_year not found. Exception: {e}")
            await browser.close()
            return

        # Fill in the form and trigger the search
        await page.select_option("#filing_year", "2025")
        await page.click("#agree_statement")
        await page.click("#search_button")

        # Wait for search results to load
        await page.wait_for_load_state("networkidle")

        # Get page content
        content = await page.content()
        matches = []

        # Check for relevant search terms
        for term in SEARCH_TERMS:
            if term.lower() in content.lower():
                matches.append(term)

        # If matches found, send a Pushover alert
        if matches:
            message = f"Senate disclosures mention: {', '.join(matches)}"
            await send_pushover_notification(message)
        else:
            print("No UNH-related terms found.")

        await browser.close()


async def send_pushover_notification(message):
    if not PUSHOVER_API_TOKEN or not PUSHOVER_USER_KEY:
        print("Missing Pushover credentials")
        return

    import httpx
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": PUSHOVER_API_TOKEN,
                "user": PUSHOVER_USER_KEY,
                "message": message
            }
        )
        print(f"Pushover response: {response.status_code} - {response.text}")


if __name__ == "__main__":
    asyncio.run(scan_senate_disclosures())