import os
import requests
from bs4 import BeautifulSoup
import pdfplumber
import pytesseract
from pdf2image import convert_from_path

# Load secrets from GitHub Actions environment
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")

# Define keywords to search for
KEYWORDS = ["UNH", "UnitedHealth"]

# House disclosure landing page (placeholder for now)
HOUSE_DISCLOSURE_URL = "https://disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure"

def send_notification(message):
    if not PUSHOVER_API_TOKEN or not PUSHOVER_USER_KEY:
        print("❌ Missing Pushover credentials.")
        return
    try:
        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": PUSHOVER_API_TOKEN,
                "user": PUSHOVER_USER_KEY,
                "title": "UNH Disclosure Alert",
                "message": message
            }
        )
        if response.ok:
            print("✅ Pushover notification sent.")
        else:
            print(f"❌ Pushover error: {response.text}")
    except Exception as e:
        print(f"⚠️ Failed to send Pushover alert: {e}")

def download_pdf(url, filename):
    try:
        response = requests.get(url)
        with open(filename, 'wb') as f:
            f.write(response.content)
        print(f"📥 Downloaded {filename}")
        return filename
    except Exception as e:
        print(f"⚠️ Error downloading PDF: {e}")
        return None

def extract_text_from_pdf(pdf_path):
    try:
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        if not text.strip():
            raise ValueError("pdfplumber failed, falling back to OCR")
        return text
    except Exception as e:
        print(f"⚠️ pdfplumber failed on {pdf_path}: {e}")
        return ocr_pdf(pdf_path)

def ocr_pdf(pdf_path):
    try:
        images = convert_from_path(pdf_path)
        return "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception as e:
        print(f"⚠️ OCR failed on {pdf_path}: {e}")
        return ""

def scan_text_for_keywords(text):
    return any(keyword.lower() in text.lower() for keyword in KEYWORDS)

def main():
    # TODO: Replace with real PDF scraping logic
    test_pdf_url = "https://www.ethics.senate.gov/downloads/your-test-disclosure.pdf"
    local_filename = "house_disclosure.pdf"

    if download_pdf(test_pdf_url, local_filename):
        text = extract_text_from_pdf(local_filename)
        if scan_text_for_keywords(text):
            send_notification("🕵️ UNH-related trade found in House disclosure!")
        else:
            print("🔍 No UNH mentions found.")
    else:
        print("❌ Failed to download or process PDF.")

if __name__ == "__main__":
    main()