import fitz  # PyMuPDF
from app.agents.text_cleaner import clean_document_pages
from app.storage.s3 import download_from_s3
from app.core.logging import get_logger

log = get_logger(__name__)

# A PDF with fewer than this many characters per page on average is treated as scanned
_MIN_CHARS_PER_PAGE = 50


def classify_document(state: dict) -> dict:
    """
    LangGraph node: detect document type and extract text for born-digital PDFs.

    Reads:  state["s3_key"], state["filename"], state["job_id"]

    Returns one of:
      {"doc_type": "pdf_text",    "raw_text": str}  — born-digital PDF; text extracted here
      {"doc_type": "pdf_scanned"}                   — scanned PDF; OCR node handles extraction
      {"doc_type": "image"}                          — standalone image file
      {"error": str}                                 — unrecoverable failure
    """
    try:
        content  = download_from_s3(state["s3_key"])
        filename = state["filename"].lower()

        if filename.endswith(".pdf"):
            doc       = fitz.open(stream=content, filetype="pdf")
            num_pages = len(doc)
            page_texts = [page.get_text("text") for page in doc]
            total_chars = sum(len(t) for t in page_texts)
            avg_chars   = total_chars / max(num_pages, 1)

            if avg_chars >= _MIN_CHARS_PER_PAGE:
                full_text = clean_document_pages(page_texts)
                log.info("classified_pdf_text",
                         job_id=state["job_id"], pages=num_pages,
                         avg_chars=round(avg_chars, 1))
                return {"doc_type": "pdf_text", "raw_text": full_text}
            else:
                log.info("classified_pdf_scanned",
                         job_id=state["job_id"], pages=num_pages,
                         avg_chars=round(avg_chars, 1))
                return {"doc_type": "pdf_scanned"}

        elif filename.endswith((".png", ".jpg", ".jpeg", ".tiff")):
            log.info("classified_image", job_id=state["job_id"])
            return {"doc_type": "image"}

        else:
            return {"error": f"classifier: unrecognised extension in '{state['filename']}'"}

    except Exception as exc:
        log.error("classifier_failed", job_id=state["job_id"], error=str(exc), exc_info=True)
        return {"error": f"classifier: {exc}"}
