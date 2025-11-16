import os
import csv
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import re
from datetime import datetime

# Set tesseract path if needed (Windows)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# --- Extraction helpers ---
def extract_vendor(text: str) -> str:
    # First non-empty line as vendor
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return lines[0] if lines else "Unknown"

def extract_date(text: str) -> str:
    text = clean_text_for_dates(text)
    date_patterns = [
            r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',   # 5/1/2014, 05-01-14
            r'\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b'      # 2014-05-01
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[ .-]?\d{1,2},?[ .-]?\d{2,4}\b',  # May 1, 2014
        ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw_date = match.group(0)
        
            possible_formats = [
                "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y",
                    "%Y-%m-%d", "%Y/%m/%d",
                    "%b %d %Y", "%B %d %Y",
                    "%b %d, %Y", "%B %d, %Y",
            ]

            for fmt in possible_formats:
                try:
                    parsed = datetime.strptime(raw_date, fmt)
                    return parsed.strftime("%Y-%m-%d")
                except ValueError:
                    continue
    
    return "Not found"

def extract_total(text: str) -> str:

    total_pattern = r'(?:TOTAL|Amount Due)[^\d]{0,10}(\d+[.,]\d{2})'

    match = re.search(total_pattern, text, re.IGNORECASE)
    if match:
        return match.group(1)
    
    money_pattern = r'\$[\d,]+\.\d{2}'
    money_matches = re.findall(money_pattern, text)
    
    if money_matches:
        return money_matches[-1]
    return "Not found"

def clean_text_for_dates(text: str) -> str:
    return(
        text.replace("I", "1")
            .replace("l", "1")
            .replace("O", "0")
    )

def preprecess_image(image: Image.Image) -> Image.Image:
    # Convert to Greyscale
    image = image.convert("L")

    return image

# --- Batch processing ---
def process_invoices(folder: str, output_csv: str = "results.csv"):
    results = []
    for filename in os.listdir(folder):
        if not filename.lower().endswith(".pdf"):
            continue

        filepath = os.path.join(folder, filename)
        print(f"Processing: {filename}")

        try:
            images = convert_from_path(filepath)
            text = ""
            for i, img in enumerate(images):
                text += pytesseract.image_to_string(img, config="--psm 6") + "\n"

            vendor = extract_vendor(text)
            date = extract_date(text)
            total = extract_total(text)

            results.append([filename, vendor, date, total])

        except Exception as e:
            print(f"Error processing {filename}: {e}")
            results.append([filename, "ERROR", "ERROR", "ERROR"])

    # Save to CSV
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Filename", "Vendor", "Date", "Total"])
        writer.writerows(results)

    print(f"✅ Finished! Results saved to {output_csv}")


if __name__ == "__main__":
    process_invoices("invoices")  # put your PDFs in a folder called "invoices"
