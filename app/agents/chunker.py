from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import List, Dict, Any

from app.core.config import settings
from app.core.logging import get_logger
from app.agents.text_cleaner import clean_document_text

log = get_logger(__name__)


def chunk_document(state: dict) -> dict:
    """
    LangGraph node: split document text into parent and child chunks.

    Input sources (combined in this order):
      1. state["raw_text"]       — plain text from classifier or OCR (may be None)
      2. state["image_captions"] — appended as "[Page N image: {caption}]" blocks

    Parent chunks (~PARENT_CHUNK_SIZE chars) provide rich context for the LLM.
    Child chunks (~CHILD_CHUNK_SIZE chars) are the retrieval targets for search.

    Chunk ID format:
      parent: "{job_id[:8]}-p{parent_index}"
      child:  "{parent_id}-c{child_index}"

    Reads:  state["raw_text"], state["image_captions"], state["job_id"]
    Writes: state["chunks"]

    Returns:
      {"chunks": List[Dict]}
      {"error": str}  if no text is available after combining all sources
    """
    try:
        parts: List[str] = []

        raw = (state.get("raw_text") or "").strip()
        if raw:
            parts.append(raw)

        for cap in (state.get("image_captions") or []):
            text = (cap.get("caption") or "").strip()
            if text:
                parts.append(f"[Page {cap['page']} image: {text}]")

        if not parts:
            return {"error": "chunker: no text or captions available"}

        full_text = clean_document_text("\n\n".join(parts))

        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.PARENT_CHUNK_SIZE,
            chunk_overlap=settings.PARENT_CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHILD_CHUNK_SIZE,
            chunk_overlap=settings.CHILD_CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        parent_docs = parent_splitter.create_documents([full_text])
        prefix      = state["job_id"][:8]
        chunks: List[Dict[str, Any]] = []

        for p_idx, parent_doc in enumerate(parent_docs):
            parent_id = f"{prefix}-p{p_idx}"
            chunks.append({
                "id":        parent_id,
                "text":      parent_doc.page_content,
                "type":      "parent",
                "parent_id": None,
            })
            for c_idx, child_doc in enumerate(
                child_splitter.create_documents([parent_doc.page_content])
            ):
                chunks.append({
                    "id":        f"{parent_id}-c{c_idx}",
                    "text":      child_doc.page_content,
                    "type":      "child",
                    "parent_id": parent_id,
                })

        parents  = sum(1 for c in chunks if c["type"] == "parent")
        children = sum(1 for c in chunks if c["type"] == "child")
        log.info("chunking_complete",
                 job_id=state["job_id"], parents=parents, children=children)
        return {"chunks": chunks}

    except Exception as exc:
        log.error("chunker_failed", job_id=state["job_id"],
                  error=str(exc), exc_info=True)
        return {"error": f"chunker: {exc}"}
