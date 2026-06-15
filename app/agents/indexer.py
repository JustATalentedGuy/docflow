from redis import Redis

from app.storage.qdrant import upsert_chunks
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def index_chunks(state: dict) -> dict:
    """
    LangGraph node: persist chunks to Qdrant and mark BM25 index as stale.

    Steps:
      1. Validate chunks and embeddings lists are non-empty and same length.
      2. Call upsert_chunks() — writes to Qdrant (parent + child).
      3. Set Redis key "bm25:dirty" = "1" (no TTL).
         app/workers/tasks.py reads this flag after pipeline completion and
         rebuilds the in-memory BM25 index in the HybridSearcher singleton.

    Reads:  state["chunks"], state["embeddings"], state["job_id"], state["filename"]
    Writes: state["indexed"]

    Returns:
      {"indexed": True}
      {"error": str}  on failure
    """
    try:
        chunks     = state.get("chunks") or []
        embeddings = state.get("embeddings") or []

        if not chunks:
            return {"error": "indexer: no chunks to index"}
        if len(chunks) != len(embeddings):
            return {"error": (
                f"indexer: length mismatch — "
                f"{len(chunks)} chunks vs {len(embeddings)} embeddings"
            )}

        upsert_chunks(
            chunks=chunks,
            embeddings=embeddings,
            job_id=state["job_id"],
            user_id=state["user_id"],
            file_id=state["file_id"],
            filename=state["filename"],
        )

        # Signal the Celery task to rebuild BM25 after this pipeline completes
        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis.set("bm25:dirty", "1")
        redis.close()

        log.info("indexing_complete",
                 job_id=state["job_id"], total_chunks=len(chunks))
        return {"indexed": True}

    except Exception as exc:
        log.error("indexer_failed", job_id=state["job_id"],
                  error=str(exc), exc_info=True)
        return {"error": f"indexer: {exc}"}
