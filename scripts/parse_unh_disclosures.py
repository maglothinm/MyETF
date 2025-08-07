import os
import requests
import smtplib
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from PyPDF2 import PdfReader
from urllib.parse import urljoin  # ✅ Fixes broken URL joins

# Gmail credentials from GitHub Secrets
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Search terms
SEARCH_TERMS = ["UNH", "UnitedHealth"]

# Disclosure URLs
HOUSE_URL = "https://disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure"
SENATE_URL = "https://efdsearch.senate.gov/search/"

def send_email(match_found, hits):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 UNH Disclosure Alert - {datetime.now().strftime('%Y-%m-%d')}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS

    if match_found:
        body = f"<h2>Mentions of UNH or UnitedHealth found in filings:</h2><ul>"
        for hit in hits:
            body += f"<li><a href='{hit}'>{hit}</a></li>"
        body += "</ul>"
    else:
        body = "<p>No references to UNH or UnitedHealth were found in today's PDFs.</p>"

    body += f"""
    <hr>
    <p>Manual disclosure sites:</p>
    <ul>
      <li><a href="{HOUSE_URL}">House Disclosures</a></li>
      <li><a href="{SENATE_URL}">Senate Disclosures</a></li>
    </ul>
    """

    msg.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
            print("✅ Email sent.")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

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
            urljoin(HOUSE_URL, link["href"])
            for link in links
            if link["href"].endswith(".pdf")
        ]
        return pdf_urls[:10]  # Only check the most recent 10 filings
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
            urljoin(SENATE_URL, link["href"])
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
        send_email(True, matched)
    else:
        print("ℹ️ No matches found.")
        send_email(False, [])

if __name__ == "__main__":
    main()