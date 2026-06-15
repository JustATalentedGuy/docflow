from celery import Celery
from redis import Redis

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

# Configure structured logging for the worker process at import time
configure_logging()
log = get_logger(__name__)

celery_app = Celery(
    "docflow",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,              # Acknowledge only after completion
    task_reject_on_worker_lost=True,  # Re-queue if worker dies mid-task
    worker_prefetch_multiplier=1,     # One task at a time per worker (memory safety)
    task_track_started=True,
    task_soft_time_limit=600,         # 10-minute soft limit per task
    task_time_limit=660,              # 11-minute hard kill
)


def _get_redis() -> Redis:
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)


@celery_app.task(
    bind=True,
    name="docflow.process_document",
    max_retries=3,
    default_retry_delay=15,
)
def process_document(self, job_id: str, user_id: str, file_id: str, s3_key: str, filename: str) -> dict:
    """
    Run the full LangGraph document processing pipeline asynchronously.

    Args:
        job_id:   UUID string (matches Redis key "job:{job_id}").
        s3_key:   S3 object key of the raw uploaded file.
        filename: Original filename (used for type detection and source attribution).

    Status transitions in Redis (key "job:{job_id}", TTL 24 h):
        queued → processing → completed
        queued → processing → failed:{reason}

    BM25 rebuild:
        After successful indexing the indexer sets "bm25:dirty"="1" in Redis.
        This task rebuilds the in-memory BM25 index in the HybridSearcher
        singleton (shared with the FastAPI process via the same Python process),
        then deletes the flag.

        Note: the searcher singleton is only shared within the SAME process.
        If the API and worker run in separate containers (standard Docker Compose
        setup), the BM25 index in the API container is rebuilt at its next
        startup or the next task that runs in the API process. For a single
        all-in-one process, both share the same searcher object.
        In the provided docker-compose, they are separate containers, so the
        BM25 index in the API process is rebuilt on the next API restart or
        when main.py lifespan runs load_all_child_chunks().
        The rebuild here updates the worker's own copy for any search calls
        routed to the worker (none in current design, but kept for completeness).
    """
    # Import inside function to avoid circular imports at module load time
    from app.agents.graph import PIPELINE, DocumentState
    from app.storage.qdrant import load_all_child_chunks
    from app.search.hybrid import searcher
    from app.db import get_db, utc_now

    redis = _get_redis()
    try:
        redis.setex(f"job:{job_id}", 86400, "processing")
        with get_db() as db:
            db.execute(
                "UPDATE files SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                ("processing", utc_now(), file_id, user_id),
            )
        log.info("task_started", job_id=job_id, filename=filename)

        initial_state: DocumentState = {
            "job_id":         job_id,
            "user_id":        user_id,
            "file_id":        file_id,
            "s3_key":         s3_key,
            "filename":       filename,
            "doc_type":       None,
            "raw_text":       None,
            "image_captions": None,
            "chunks":         None,
            "embeddings":     None,
            "indexed":        False,
            "error":          None,
        }

        final_state = PIPELINE.invoke(initial_state)

        if final_state.get("error"):
            reason = str(final_state["error"])[:120]
            redis.setex(f"job:{job_id}", 86400, f"failed:{reason}")
            with get_db() as db:
                db.execute(
                    "UPDATE files SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                    (f"failed:{reason}", utc_now(), file_id, user_id),
                )
            log.error("pipeline_failed", job_id=job_id, reason=reason)
            return {"success": False, "error": reason}

        # Rebuild BM25 index if new chunks were indexed
        if redis.get("bm25:dirty") == "1":
            texts, ids, metadatas = load_all_child_chunks()
            if texts:
                searcher.build_bm25_index(texts, ids, metadatas)
                log.info("bm25_rebuilt", num_chunks=len(texts))
            redis.delete("bm25:dirty")

        chunks_indexed = len(final_state.get("chunks") or [])
        redis.setex(f"job:{job_id}", 86400, "completed")
        with get_db() as db:
            db.execute(
                "UPDATE files SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                ("completed", utc_now(), file_id, user_id),
            )
        log.info("task_completed", job_id=job_id, chunks_indexed=chunks_indexed)
        return {"success": True, "chunks_indexed": chunks_indexed}

    except Exception as exc:
        log.error("task_exception", job_id=job_id, error=str(exc), exc_info=True)
        redis.setex(f"job:{job_id}", 86400, "failed:exception")
        with get_db() as db:
            db.execute(
                "UPDATE files SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                ("failed:exception", utc_now(), file_id, user_id),
            )
        raise self.retry(exc=exc)
    finally:
        redis.close()
