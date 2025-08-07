import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from PyPDF2 import PdfReader

# Pushover credentials from GitHub Secrets
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")

# Search terms
SEARCH_TERMS = ["UNH", "UnitedHealth"]

# Disclosure URLs
HOUSE_URL = "https://disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure"
SENATE_URL = "https://efdsearch.senate.gov/search/"

def send_notification(match_found, hits):
    if not PUSHOVER_API_TOKEN or not PUSHOVER_USER_KEY:
        print("❌ Missing Pushover credentials.")
        return

    if match_found:
        message = f"📈 UNH Alert: Found {len(hits)} matching disclosure(s).\n"
        message += "\n".join(hits[:3])  # Include top 3 hits
    else:
        message = "ℹ️ No UNH matches found in today's disclosures."

    try:
        response = requests.post("https://api.pushover.net/1/messages.json", data={
            "token": PUSHOVER_API_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "title": "UNH Disclosure Scan",
            "message": message,
            "url": HOUSE_URL,
            "url_title": "House Disclosures"
        })
        response.raise_for_status()
        print("✅ Pushover notification sent.")
    except Exception as e:
        print(f"❌ Failed to send Pushover notification: {e}")

def extract_text_from_pdf(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        with open("temp.pdf", "wb") as f:
            f.write(response.content)

        reader = PdfReader("temp.pdf")
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() or ""
        return full_text
    except Exception as e:
        print(f"⚠️ Could not extract PDF text from {url}: {e}")
        return ""

def fetch_house_disclosures():
    print("🔍 Scanning House disclosures...")
    try:
        res = requests.get(HOUSE_URL)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        links = soup.find_all("a", href=True)
        pdf_urls = [
            "https://disclosures-clerk.house.gov" + link["href"]
            for link in links
            if link["href"].endswith(".pdf") and link["href"].startswith("/public_disc/financial-pdfs/")
        ]
        return pdf_urls[:10]
    except Exception as e:
        print(f"❌ House fetch failed: {e}")
        return []

def fetch_senate_disclosures():
    print("🔍 Scanning Senate disclosures...")
    try:
        res = requests.get(SENATE_URL)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        links = soup.find_all("a", href=True)
        pdf_urls = [
            "https://efdsearch.senate.gov" + link["href"]
            for link in links
            if link["href"].endswith(".pdf")
        ]
        return pdf_urls[:10]
    except Exception as e:
        print(f"❌ Senate fetch failed: {e}")
        return []

def main():
    house_pdfs = fetch_house_disclosures()
    senate_pdfs = fetch_senate_disclosures()
    all_pdfs = house_pdfs + senate_pdfs

    matched = []
    for pdf_url in all_pdfs:
        text = extract_text_from_pdf(pdf_url)
        if any(term.lower() in text.lower() for term in SEARCH_TERMS):
            matched.append(pdf_url)

    if matched:
        print(f"✅ Found {len(matched)} matching disclosures.")
        send_notification(True, matched)
    else:
        print("ℹ️ No matches found.")
        send_notification(False, [])

if __name__ == "__main__":
    main()