import os
import requests
import zipfile
import io
from datetime import datetime
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from pushover import Client

# CONFIG
SENATE_ZIP_URL = "https://www.ethics.senate.gov/public/_cache/files/ea40e7df-3bc7-4d71-b33e-338c63e66fc7/2025-public-financial-disclosure-reports.zip"
KEYWORDS = ["UNH", "UnitedHealth", "United Health Group"]
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
TEMP_DIR = "senate_pdfs"
LOG_FILE = "senate_unh_matches.log"

# Ensure temp dir exists
os.makedirs(TEMP_DIR, exist_ok=True)

# Helper: Send Pushover alert
def send_pushover_alert(title, message):
    try:
        client = Client(user_key=PUSHOVER_USER_KEY, api_token=PUSHOVER_API_TOKEN)
        client.send_message(message, title=title)
    except Exception as e:
        print(f"❌ Failed to send Pushover alert: {e}")

# Step 1: Download and extract Senate ZIP
def download_and_extract_zip(zip_url):
    print(f"📥 Downloading ZIP from: {zip_url}")
    r = requests.get(zip_url)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(TEMP_DIR)
    return [os.path.join(TEMP_DIR, f) for f in os.listdir(TEMP_DIR) if f.endswith(".pdf")]

# Step 2: Extract text from PDF (OCR fallback)
def extract_text_from_pdf(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return text.strip()
    except Exception as e:
        print(f"⚠️ pdfplumber failed on {pdf_path}: {e}")
        return ""

def ocr_pdf(pdf_path):
    try:
        images = convert_from_path(pdf_path)
        return "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception as e:
        print(f"⚠️ OCR failed on {pdf_path}: {e}")
        return ""

# Step 3: Search for keyword match
def search_keywords(text):
    lines = text.splitlines()
    for line in lines:
        if any(keyword.lower() in line.lower() for keyword in KEYWORDS):
            return line
    return None

# Step 4: Main logic
def main():
    pdf_files = download_and_extract_zip(SENATE_ZIP_URL)
    print(f"🔍 Extracted {len(pdf_files)} PDFs")

    for pdf in pdf_files:
        text = extract_text_from_pdf(pdf)
        if len(text.strip()) < 100:
           try:
              text = ocr_pdf(pdf)
           except Exception as e:
              print(f"❌ ocr_pdf failed outside function scope: {e}")
              text = ""

        match_line = search_keywords(text)
        if match_line:
            filename = os.path.basename(pdf)
            print(f"✅ MATCH: {filename}\n{match_line}")
            send_pushover_alert(
                "Senate UNH Disclosure Found",
                f"File: {filename}\n\nMatched line:\n{match_line}"
            )
            with open(LOG_FILE, "a") as log:
                log.write(f"{datetime.now()} | {filename} | {match_line}\n")

if __name__ == "__main__":
    main()