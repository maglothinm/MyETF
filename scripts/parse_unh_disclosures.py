import os
import requests
import tempfile
import fitz  # PyMuPDF
import re
from bs4 import BeautifulSoup

# Pushover credentials
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")

# Keywords to search for
KEYWORDS = ["UNH", "UnitedHealth"]

# URLs
HOUSE_BASE = "https://disclosures-clerk.house.gov"
HOUSE_INDEX = f"{HOUSE_BASE}/PublicDisclosure/FinancialDisclosure"
SENATE_INDEX = "https://www.ethics.senate.gov/public/index.cfm/financial-disclosure-reports"

def send_pushover_notification(message):
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
    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()

        hits = []
        for keyword in keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
                hits.append(keyword)
        return hits
    except Exception as e:
        print(f"Failed to read PDF {file_path}: {e}")
        return []

def get_house_disclosure_urls(limit=5):
    try:
        response = requests.get(HOUSE_INDEX, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"/public_disc/ptr-pdfs/\d{4}/\d+\.pdf"))
        urls = [HOUSE_BASE + link["href"] for link in links]
        return urls[:limit]
    except Exception as e:
        print(f"Failed to scrape House disclosures: {e}")
        return []

def get_senate_disclosure_urls(limit=5):
    try:
        response = requests.get(SENATE_INDEX, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"serve\?File_id=[\w-]+"))
        urls = [f"https://www.ethics.senate.gov/public/index.cfm/files/serve?File_id={link['href'].split('=')[-1]}" for link in links]
        return urls[:limit]
    except Exception as e:
        print(f"Failed to scrape Senate disclosures: {e}")
        return []

def main():
    urls = get_house_disclosure_urls(limit=5) + get_senate_disclosure_urls(limit=5)
    print(f"Scanning {len(urls)} PDFs...")

    found_alerts = []

    for url in urls:
        print(f"Processing: {url}")
        pdf_path = download_pdf(url)
        if pdf_path:
            hits = scan_pdf(pdf_path, KEYWORDS)
            if hits:
                alert = f"Match for {', '.join(hits)} in:\n{url}"
                print(alert)
                found_alerts.append(alert)
            os.unlink(pdf_path)

    if found_alerts:
        send_pushover_notification("\n\n".join(found_alerts))
    else:
        print("No UNH-related trades found.")

if __name__ == "__main__":
    main()