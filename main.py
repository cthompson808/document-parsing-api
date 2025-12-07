from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
import pytesseract
from PIL import Image
import io
import re
from datetime import datetime
from pdf2image import convert_from_bytes
from typing import Optional, Tuple, List
from database import SessionLocal, Invoice
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Document Parsing API", version="0.3")

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Document Parsing API",
        version="1.0.0",
        description="API for extracting invoice vendor/date/total from documents.",
        routes=app.routes,
    )

    # Add API Key header auth to schema
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "x-api-key"
        }
    }

    # Apply security globally to all endpoints
    for path in openapi_schema["paths"]:
        for method in openapi_schema["paths"][path]:
            # Only protect actual API endpoints, not docs
            if path not in ["/ping", "/docs", "/openapi.json"]:
                openapi_schema["paths"][path][method]["security"] = [{"APIKeyHeader": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# -----------------------------
# MIDDLEWARE
# -----------------------------

@app.middleware("http")
async def require_api_key(request: Request, call_next):
    # Public endpoints that do NOT require API keys
    open_paths = ["/ping", "/docs", "/openapi.json"]

    if request.url.path not in open_paths:
        client_key = request.headers.get("x-api-key")
        server_key = os.getenv("API_KEY")

        if client_key != server_key:
            return JSONResponse(
                {"error": "Unauthorized – invalid or missing API key"},
                status_code=401
            )

    return await call_next(request)


# -----------------------------
# ROUTES
# -----------------------------

@app.get("/ping")
def ping():
    return {"status": "ok", "message": "API is running"}

@app.post("/parse")
async def parse_document(file: UploadFile = File(...)):
    content = await file.read()
    extracted_text = ""

    try:
        if file.filename.lower().endswith(".pdf"):
            # Convert PDF to images
            images = convert_from_bytes(content)
            for i, img in enumerate(images):
                extracted_text += f"\n--- Page {i + 1} ---\n"
                extracted_text += pytesseract.image_to_string(img, config="--oem 3 --psm 4")
        else:
            image = Image.open(io.BytesIO(content))
            image = preprocess_image(image)
            extracted_text = pytesseract.image_to_string(image, config="--oem 3 --psm 6")

        extracted_text = strip_page_markers(extracted_text)

        # 🔹 Updated extraction functions
        vendor, vendor_candidates = extract_vendor(extracted_text)
        date = extract_date(extracted_text)
        total, total_candidates = extract_total(extracted_text)

        # Save to DB
        db = SessionLocal()
        record = Invoice(
            filename=file.filename,
            vendor=vendor,
            date=date,
            total=total,
            extracted_text=extracted_text.strip()
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        db.close()


    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({
        "filename": file.filename,
        "size_bytes": len(content),
        "vendor": vendor,
        "vendor_candidates": vendor_candidates,
        "date": date,
        "total": total,
        "total_candidates": total_candidates,
        "extracted_text": extracted_text.strip(),
    })


@app.get("/invoices")
def get_invoices():
    db = SessionLocal()
    rows = db.query(Invoice).all()
    db.close()
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "vendor": r.vendor,
            "date": r.date,
            "total": r.total
        }
        for r in rows
    ]

@app.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int):
    db = SessionLocal()
    record = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    db.close()

    if not record:
        return JSONResponse({"error": "Not found"}, status_code=404)

    return {
        "id": record.id,
        "filename": record.filename,
        "vendor": record.vendor,
        "date": record.date,
        "total": record.total,
        "extracted_text": record.extracted_text
    }


# -----------------------------
# TEXT CLEANING & PREPROCESSING
# -----------------------------

def preprocess_image(image: Image.Image) -> Image.Image:
    image = image.convert("L")  # grayscale
    return image

def clean_text_for_dates(text: str) -> str:
    return (
        text.replace("I", "1")
            .replace("l", "1")
            .replace("O", "0")
    )

def clean_ocr_text(text: str) -> str:
    if not text:
        return ""
    s = text.replace('\r', '\n')
    return s

# -----------------------------
# DATE EXTRACTION (Your version + fix)
# -----------------------------

def extract_date(text: str) -> str:
    text = clean_text_for_dates(text)
    date_patterns = [
        r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',  # 5/1/2014, 05-01-14
        r'\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b',    # 2014-05-01
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

# -----------------------------
# VENDOR EXTRACTION (Improved)
# -----------------------------

VENDOR_SKIP_KEYWORDS = re.compile(
    r'^(invoice|invoice no|invoice #|invoice number|date|due|page|total|tax|phone|tel|fax|bill to|ship to|amount|balance|subtotal|description|item|quantity|qty|account|address|order|ship|email|page)',
    re.IGNORECASE
)
COMPANY_HINTS = re.compile(r'\b(inc|llc|ltd|co|corp|corporation|company|gmbh|plc)\b', re.IGNORECASE)

def strip_page_markers(text: str) -> str:
    """Remove artificial page headers like --- Page 1 --- added during PDF conversion."""
    return re.sub(r'\n?-{2,}\s*Page\s*\d+\s*-{2,}\n?', '\n', text, flags=re.IGNORECASE)

def extract_vendor(text: str, top_n_lines: int = 8) -> Tuple[str, List[str]]:
    text = clean_ocr_text(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidates = []

    for i, line in enumerate(lines[:top_n_lines]):
        lc = line.lower()
        if VENDOR_SKIP_KEYWORDS.search(lc):
            continue
        if re.match(r'^[\d\W]{1,}$', line):
            continue
        if '@' in line or 'http' in line or 'www' in line:
            continue

        score = 0
        if COMPANY_HINTS.search(line):
            score += 5
        if 1 < len(line.split()) <= 6:
            score += 2
        letters = sum(c.isalpha() for c in line)
        digits = sum(c.isdigit() for c in line)
        if letters > digits:
            score += 1

        candidates.append((score, i, line))

    if candidates:
        candidates.sort(key=lambda x: (-x[0], x[1]))
        chosen = candidates[0][2]
        return chosen, [c[2] for c in candidates]

    return (lines[0] if lines else "Unknown"), []

# -----------------------------
# TOTAL EXTRACTION (Improved)
# -----------------------------

TOTAL_KEYWORDS = r'(?:grand total|total due|amount due|balance due|amount payable|net total|invoice total|total amount|total)'
MONEY_PATTERN = r'[\$]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})'

def parse_amount_to_float(amount_str: str) -> Optional[float]:
    if not amount_str:
        return None
    s = re.sub(r'[^\d,.\-]', '', amount_str.strip())
    if s == '':
        return None
    if s.count(',') > 0 and s.count('.') > 0:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif s.count(',') > 0 and s.count('.') == 0:
        if len(s.split(',')[-1]) == 2:
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')
    try:
        return float(s)
    except Exception:
        return None

def extract_total(text: str, gap_max_chars: int = 80) -> Tuple[str, List[str]]:
    text = clean_ocr_text(text)
    regex = re.compile(rf'{TOTAL_KEYWORDS}[^\d]{{0,{gap_max_chars}}}({MONEY_PATTERN})', re.IGNORECASE | re.DOTALL)
    match = regex.search(text)
    debug_candidates = []

    if match:
        amt_raw = match.group(1)
        parsed = parse_amount_to_float(amt_raw)
        debug_candidates.append(amt_raw)
        if parsed is not None:
            return f"{parsed:.2f}", debug_candidates

    money_matches = re.findall(MONEY_PATTERN, text)
    debug_candidates.extend(money_matches)
    if money_matches:
        parsed_list = [parse_amount_to_float(m) for m in money_matches if parse_amount_to_float(m) is not None]
        if parsed_list:
            return f"{max(parsed_list):.2f}", debug_candidates
    return "Not found", debug_candidates
