import io
import fitz          # PyMuPDF — renders PDF pages as PIL Images
import pytesseract
from PIL import Image

from app.agents.text_cleaner import clean_document_pages
from app.storage.s3 import download_from_s3
from app.core.logging import get_logger

log = get_logger(__name__)

# DPI for rasterising PDF pages before OCR.
# 200 DPI is a good balance: readable by Tesseract, manageable on 1 GB RAM.
_PDF_RENDER_DPI = 200

# Tesseract config: OEM 3 = LSTM engine, PSM 3 = auto page-segmentation.
# These are the defaults but being explicit avoids surprises across environments.
_TESSERACT_CONFIG = "--oem 3 --psm 3"


def extract_text(state: dict) -> dict:
    """
    LangGraph node: extract text from a scanned PDF using Tesseract OCR.

    Only called when state["doc_type"] == "pdf_scanned".

    Reads:  state["s3_key"], state["job_id"]
    Writes: state["raw_text"]

    Process per page:
      1. Render PDF page to PIL Image at _PDF_RENDER_DPI via PyMuPDF.
      2. Run pytesseract on the PIL Image (calls system tesseract binary).
      3. Collect per-page strings and join with double newlines.

    Returns:
      {"raw_text": str}  on success
      {"error": str}     on failure
    """
    try:
        content = download_from_s3(state["s3_key"])
        doc = fitz.open(stream=content, filetype="pdf")
        page_texts = []

        for page_num, page in enumerate(doc):
            mat = fitz.Matrix(_PDF_RENDER_DPI / 72, _PDF_RENDER_DPI / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            text = pytesseract.image_to_string(img, lang="eng",
                                               config=_TESSERACT_CONFIG)
            page_texts.append(text)
            log.info("ocr_page_done",
                     job_id=state["job_id"],
                     page=page_num + 1,
                     total=len(doc),
                     chars=len(text))

        raw_text = clean_document_pages(page_texts)
        log.info("ocr_complete", job_id=state["job_id"], total_chars=len(raw_text))
        return {"raw_text": raw_text}

    except Exception as exc:
        log.error("ocr_failed", job_id=state["job_id"],
                  error=str(exc), exc_info=True)
        return {"error": f"ocr: {exc}"}
