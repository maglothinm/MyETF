import os
import requests
from bs4 import BeautifulSoup
from pushover_complete import PushoverAPI
import pdfplumber
import pytesseract
from pdf2image import convert_from_path

# Define keywords to search for
KEYWORDS = ["UNH", "UnitedHealth"]

# Senate disclosure URL (example — replace with correct one if needed)
SENATE_DISCLOSURE_URL = "https://www.ethics.senate.gov/public/index.cfm/financial-disclosure"


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
    # TODO: Replace with actual PDF URL scraping logic
    test_pdf_url = "https://www.ethics.senate.gov/downloads/your-test-disclosure.pdf"
    local_filename = "senate_disclosure.pdf"

    if download_pdf(test_pdf_url, local_filename):
        text = extract_text_from_pdf(local_filename)
        if scan_text_for_keywords(text):
            send_notification("🕵️ UNH-related trade found in Senate disclosure!")
        else:
            print("🔍 No UNH mentions found.")
    else:
        print("❌ Failed to download or process PDF.")

if __name__ == "__main__":
    main()