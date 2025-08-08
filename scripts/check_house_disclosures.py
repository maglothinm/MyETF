import os
import zipfile
import requests
import tempfile
import shutil
import pdfplumber
from pushover import Client

# === CONFIGURATION ===

HOUSE_ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025FD.ZIP"
KEYWORDS = ["UNH", "UnitedHealth"]

# Get Pushover credentials from GitHub Secrets or local environment
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")


def download_and_extract_zip(zip_url):
    """Download a ZIP file and extract PDFs to a temp folder."""
    tmp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(tmp_dir, "disclosures.zip")

    print(f"⬇️ Downloading ZIP from {zip_url}...")
    response = requests.get(zip_url)
    with open(zip_path, "wb") as f:
        f.write(response.content)

    print("📦 Extracting ZIP contents...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(tmp_dir)

    pdf_files = [
        os.path.join(tmp_dir, f)
        for f in os.listdir(tmp_dir)
        if f.lower().endswith(".pdf")
    ]

    print(f"📄 Found {len(pdf_files)} PDFs.")
    return pdf_files, tmp_dir


def extract_text_from_pdf(pdf_path):
    """Extract text using pdfplumber."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text() or ""
            return text
    except Exception as e:
        print(f"⚠️ Error reading {pdf_path}: {e}")
        return ""


def send_pushover_notification(matching_files):
    """Send Pushover alert with the matching file names."""
    if not PUSHOVER_API_TOKEN or not PUSHOVER_USER_KEY:
        print("❌ Missing Pushover credentials.")
        return

    message = f"✅ UNH match found in {len(matching_files)} House disclosures:\n"
    message += "\n".join([os.path.basename(f) for f in matching_files])

    print("📲 Sending Pushover alert...")
    Client(user_key=PUSHOVER_USER_KEY, api_token=PUSHOVER_API_TOKEN).send_message(
        message, title="House UNH Disclosure Alert"
    )


def main():
    pdf_files, tmp_dir = download_and_extract_zip(HOUSE_ZIP_URL)

    matching_files = []
    for pdf in pdf_files:
        print(f"🔍 Scanning {os.path.basename(pdf)}...")
        text = extract_text_from_pdf(pdf)
        if any(keyword in text for keyword in KEYWORDS):
            print(f"✅ Match found in {pdf}")
            matching_files.append(pdf)

    if matching_files:
        send_pushover_notification(matching_files)
    else:
        print("❌ No matches found.")

    # Clean up
    print("🧹 Cleaning up temporary files...")
    shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    main()