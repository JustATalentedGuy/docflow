from typing import TypedDict, Optional, List, Dict, Any
from langgraph.graph import StateGraph, END

from app.agents.classifier import classify_document
from app.agents.ocr import extract_text
from app.agents.captioner import caption_images
from app.agents.chunker import chunk_document
from app.agents.embedder import embed_chunks
from app.agents.indexer import index_chunks


class DocumentState(TypedDict):
    """
    Shared state passed between every agent node in the pipeline.

    Each node receives the full state dict and returns a dict containing
    only the keys it modifies. LangGraph merges the returned dict into
    the running state automatically.
    """
    # Provided by the Celery task before pipeline invocation
    job_id: str
    user_id: str
    file_id: str
    s3_key: str       # format: "uploads/{job_id}/{filename}"
    filename: str

    # Set by classifier node
    # Values: "pdf_text" | "pdf_scanned" | "image"
    doc_type: Optional[str]

    # Set by classifier (pdf_text) or ocr (pdf_scanned) nodes
    raw_text: Optional[str]

    # Set by captioner node
    # List of {"page": int, "image_index": int, "caption": str}
    # Empty list [] if no images found; None before captioner runs
    image_captions: Optional[List[Dict[str, Any]]]

    # Set by chunker node
    # List of {"id": str, "text": str, "type": "parent"|"child", "parent_id": str|None}
    chunks: Optional[List[Dict[str, Any]]]

    # Set by embedder node — parallel to chunks list
    # None at position i means chunks[i] is a parent (not embedded)
    embeddings: Optional[List[Optional[List[float]]]]

    # Set to True by indexer node on success
    indexed: bool

    # Set by any node on unrecoverable error; causes pipeline to short-circuit to END
    error: Optional[str]


# ── Routing functions ────────────────────────────────────────────────────────

def _route_after_classify(state: DocumentState) -> str:
    if state.get("error"):
        return END
    doc_type = state.get("doc_type", "pdf_scanned")
    # pdf_text: classifier already extracted raw_text; skip OCR, go straight to caption
    # image:    no text to extract; caption the whole image
    # pdf_scanned: needs OCR first
    if doc_type in ("pdf_text", "image"):
        return "caption"
    return "ocr"


def _route_after_ocr(state: DocumentState) -> str:
    return END if state.get("error") else "caption"


def _route_after_caption(state: DocumentState) -> str:
    return END if state.get("error") else "chunk"


def _route_after_chunk(state: DocumentState) -> str:
    return END if state.get("error") else "embed"


def _route_after_embed(state: DocumentState) -> str:
    return END if state.get("error") else "index"


# ── Graph assembly ───────────────────────────────────────────────────────────

def build_pipeline():
    graph = StateGraph(DocumentState)

    graph.add_node("classify", classify_document)
    graph.add_node("ocr",      extract_text)
    graph.add_node("caption",  caption_images)
    graph.add_node("chunk",    chunk_document)
    graph.add_node("embed",    embed_chunks)
    graph.add_node("index",    index_chunks)

    graph.set_entry_point("classify")

    graph.add_conditional_edges(
        "classify", _route_after_classify,
        {"ocr": "ocr", "caption": "caption", END: END},
    )
    graph.add_conditional_edges(
        "ocr", _route_after_ocr,
        {"caption": "caption", END: END},
    )
    graph.add_conditional_edges(
        "caption", _route_after_caption,
        {"chunk": "chunk", END: END},
    )
    graph.add_conditional_edges(
        "chunk", _route_after_chunk,
        {"embed": "embed", END: END},
    )
    graph.add_conditional_edges(
        "embed", _route_after_embed,
        {"index": "index", END: END},
    )
    graph.add_edge("index", END)

    return graph.compile()


# Module-level compiled pipeline singleton.
# Imported by app/workers/tasks.py.
# LangSmith traces this automatically when LANGCHAIN_TRACING_V2=true.
PIPELINE = build_pipeline()
