import os
import requests
import zipfile
import io
import re
from bs4 import BeautifulSoup
from datetime import datetime
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from pushover import Client

# CONFIG
HOUSE_BASE_URL = "https://disclosures-clerk.house.gov"
DISCLOSURE_PAGE = f"{HOUSE_BASE_URL}/PublicDisclosure/FinancialDisclosure"
KEYWORDS = ["UNH", "UnitedHealth", "United Health Group"]
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
LOG_FILE = "house_unh_matches.log"
TEMP_DIR = "temp_house_pdfs"

# Ensure temp folder exists
os.makedirs(TEMP_DIR, exist_ok=True)

def find_latest_zip_url():
    response = requests.get(DISCLOSURE_PAGE)
    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.find_all("a", href=True)
    zip_links = [l['href'] for l in links if l['href'].endswith(".zip") and "FinancialDisclosure" in l['href']]
    if not zip_links:
        print("❌ No ZIP files found on page.")
        return None
    latest_zip = sorted(zip_links, reverse=True)[0]  # Usually named by date
    return f"{HOUSE_BASE_URL}{latest_zip}"

def download_and_extract_zip(zip_url):
    r = requests.get(zip_url)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(TEMP_DIR)
    return [os.path.join(TEMP_DIR, f) for f in os.listdir(TEMP_DIR) if f.endswith(".pdf")]

def extract_text_from_pdf(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return full_text.strip()
    except Exception as e:
        print(f"⚠️ pdfplumber failed on {pdf_path}: {e}")
        return ""

def ocr_pdf(pdf_path):
    try:
        images = convert_from_path(pdf_path)
        text = "\n".join(pytesseract.image_to_string(img) for img in images)
        return text
    except Exception as e:
        print(f"⚠️ OCR failed on {pdf_path}: {e}")
        return ""

def search_keywords(text):
    lines = text.splitlines()
    for line in lines:
        if any(keyword.lower() in line.lower() for keyword in KEYWORDS):
            return line
    return None

def send_pushover_alert(title, message):
    try:
        client = Client(user_key=PUSHOVER_USER_KEY, api_token=PUSHOVER_API_TOKEN)
        client.send_message(message, title=title)
    except Exception as e:
        print(f"❌ Failed to send Pushover alert: {e}")

def main():
    zip_url = find_latest_zip_url()
    if not zip_url:
        return

    print(f"📦 Downloading ZIP: {zip_url}")
    pdf_files = download_and_extract_zip(zip_url)
    print(f"🔍 Extracted {len(pdf_files)} PDFs")

    for pdf in pdf_files:
        text = extract_text_from_pdf(pdf)
        if len(text.strip()) < 100:
            text = ocr_pdf(pdf)

        match_line = search_keywords(text)
        if match_line:
            filename = os.path.basename(pdf)
            alert_msg = f"UNH match in House filing: {filename}\n\nMatched line:\n{match_line}"
            print(f"✅ MATCH: {filename}\n{match_line}")
            send_pushover_alert("House UNH Disclosure Found", alert_msg)

            with open(LOG_FILE, "a") as log:
                log.write(f"{datetime.now()} | {filename} | {match_line}\n")

if __name__ == "__main__":
    main()
