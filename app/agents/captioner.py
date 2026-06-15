import io
import base64
import requests
import fitz
from PIL import Image
from typing import List, Dict, Any

from app.storage.s3 import download_from_s3
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_HF_API_URL = (
    f"https://api-inference.huggingface.co/models/{settings.HF_CAPTIONING_MODEL}"
)
_HF_HEADERS = {"Authorization": f"Bearer {settings.HF_API_TOKEN}"}

# Skip images smaller than these dimensions (icons, logos, decorations)
_MIN_WIDTH  = 100
_MIN_HEIGHT = 100

# Maximum images captioned per document (stays within HF free tier limits)
_MAX_IMAGES = 10


def _caption_jpeg_bytes(jpeg_bytes: bytes) -> str:
    """
    Send a JPEG image to HF Inference API (BLIP-2) and return the generated caption.
    Returns empty string on any failure — captions are best-effort, not blocking.
    """
    try:
        encoded  = base64.b64encode(jpeg_bytes).decode("utf-8")
        response = requests.post(
            _HF_API_URL,
            headers=_HF_HEADERS,
            json={"inputs": encoded},
            timeout=30,
        )
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and result:
                return result[0].get("generated_text", "")
        log.warning("hf_api_non_200",
                    status=response.status_code, body=response.text[:200])
        return ""
    except Exception as exc:
        log.warning("hf_api_error", error=str(exc))
        return ""


def caption_images(state: dict) -> dict:
    """
    LangGraph node: generate text captions for images in the document.

    Behaviour by doc_type:
      "pdf_text" / "pdf_scanned":
          Extracts embedded images from each page; captions up to _MAX_IMAGES.
          Returns {"image_captions": []} if no qualifying images are found.
      "image":
          Captions the whole file as a single image.

    Each caption dict: {"page": int, "image_index": int, "caption": str}

    Reads:  state["s3_key"], state["doc_type"], state["job_id"]
    Writes: state["image_captions"]
    """
    try:
        content  = download_from_s3(state["s3_key"])
        doc_type = state["doc_type"]
        captions: List[Dict[str, Any]] = []

        if doc_type == "image":
            caption = _caption_jpeg_bytes(content)
            captions.append({"page": 1, "image_index": 0, "caption": caption})
            log.info("image_captioned", job_id=state["job_id"])

        else:
            doc   = fitz.open(stream=content, filetype="pdf")
            count = 0

            for page_num, page in enumerate(doc):
                if count >= _MAX_IMAGES:
                    break
                for img_idx, img_info in enumerate(page.get_images(full=True)):
                    if count >= _MAX_IMAGES:
                        break
                    xref       = img_info[0]
                    base_image = doc.extract_image(xref)
                    w, h       = base_image["width"], base_image["height"]

                    if w < _MIN_WIDTH or h < _MIN_HEIGHT:
                        continue

                    pil_img = Image.open(io.BytesIO(base_image["image"])).convert("RGB")
                    buf     = io.BytesIO()
                    pil_img.save(buf, format="JPEG", quality=85)
                    caption = _caption_jpeg_bytes(buf.getvalue())

                    captions.append({
                        "page":        page_num + 1,
                        "image_index": img_idx,
                        "caption":     caption,
                    })
                    count += 1

            log.info("pdf_images_captioned",
                     job_id=state["job_id"], count=len(captions))

        return {"image_captions": captions}

    except Exception as exc:
        log.error("captioner_failed", job_id=state["job_id"],
                  error=str(exc), exc_info=True)
        return {"error": f"captioner: {exc}"}
