from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = get_logger(__name__)

# Module-level model cache — loaded on first embed_chunks() call, not at import
_model: Optional["SentenceTransformer"] = None


def _get_model() -> "SentenceTransformer":
    """Load SentenceTransformer model lazily (once per process)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _model


def embed_chunks(state: dict) -> dict:
    """
    LangGraph node: generate embeddings for all child chunks.

    Parent chunks get None (zero vector used in Qdrant storage).
    Child chunks get a List[float] of length EMBEDDING_DIMENSION.

    The returned "embeddings" list is parallel to state["chunks"]:
      embeddings[i] corresponds to chunks[i].

    Reads:  state["chunks"], state["job_id"]
    Writes: state["embeddings"]

    Returns:
      {"embeddings": List[Optional[List[float]]]}
      {"error": str}  on failure
    """
    try:
        chunks = state.get("chunks") or []
        if not chunks:
            return {"error": "embedder: no chunks to embed"}

        child_texts:   List[str] = []
        child_indices: List[int] = []
        for i, chunk in enumerate(chunks):
            if chunk["type"] == "child":
                child_texts.append(chunk["text"])
                child_indices.append(i)

        if not child_texts:
            return {"error": "embedder: no child chunks found"}

        # Batch encode; normalize for cosine similarity
        vectors = _get_model().encode(
            child_texts,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

        embeddings: List[Optional[List[float]]] = [None] * len(chunks)
        for list_pos, chunk_idx in enumerate(child_indices):
            embeddings[chunk_idx] = vectors[list_pos].tolist()

        log.info("embedding_complete",
                 job_id=state["job_id"],
                 child_chunks=len(child_texts),
                 dimension=settings.EMBEDDING_DIMENSION)
        return {"embeddings": embeddings}

    except Exception as exc:
        log.error("embedder_failed", job_id=state["job_id"],
                  error=str(exc), exc_info=True)
        return {"error": f"embedder: {exc}"}
