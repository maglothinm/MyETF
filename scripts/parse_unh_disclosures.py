import os
import requests
import tempfile
import fitz  # PyMuPDF
import re

# Get Pushover credentials from environment variables
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")

# Disclosure URLs
HOUSE_DISCLOSURE_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/"
SENATE_DISCLOSURE_URL = "https://www.ethics.senate.gov/public/index.cfm/files/serve?File_id="

# Keywords to search
KEYWORDS = ["UNH", "UnitedHealth"]

# Store matches
matches = []

def send_pushover_notification(message):
    """Send a Pushover alert."""
    if not PUSHOVER_API_TOKEN or not PUSHOVER_USER_KEY:
        print("Pushover credentials missing.")
        return

    response = requests.post("https://api.pushover.net/1/messages.json", data={
        "token": PUSHOVER_API_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "message": message
    })

    if response.status_code != 200:
        print("Failed to send Pushover notification:", response.text)
    else:
        print("Notification sent successfully.")

def download_pdf(url):
    """Download PDF from a URL and return local file path."""
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(response.content)
            return tmp_file.name
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return None

def scan_pdf(file_path, keywords):
    """Scan PDF for keyword matches."""
    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()

        matches = []
        for keyword in keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
                matches.append(keyword)
        doc.close()
        return matches
    except Exception as e:
        print(f"Failed to read PDF {file_path}: {e}")
        return []

def main():
    # Example static list for testing; replace with live scraping logic
    test_pdf_urls = [
        "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/30056581.pdf",
        "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/30056582.pdf",
    ]

    found_alerts = []

    for url in test_pdf_urls:
        print(f"Processing: {url}")
        pdf_path = download_pdf(url)
        if pdf_path:
            hits = scan_pdf(pdf_path, KEYWORDS)
            if hits:
                alert = f"Match found for {', '.join(hits)} in: {url}"
                print(alert)
                found_alerts.append(alert)
            os.unlink(pdf_path)

    if found_alerts:
        combined_message = "\n".join(found_alerts)
        send_pushover_notification(combined_message)
    else:
        print("No UNH-related trades found.")

if __name__ == "__main__":
    main()