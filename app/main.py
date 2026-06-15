from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.api.routes.upload import router as upload_router
from app.api.routes.query import router as query_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chats import router as chats_router
from app.storage.s3 import ensure_bucket_exists
from app.storage.qdrant import init_collection, load_all_child_chunks
from app.search.hybrid import searcher
from app.db import init_db

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic for the FastAPI application."""

    # 1. Structured logging must be configured before any log calls
    configure_logging()
    log.info("docflow_starting", version="1.0.0",
             environment="local" if settings.AWS_ENDPOINT_URL else "aws")

    # 2. Ensure S3 bucket exists (auto-creates on MinIO; checks on real AWS)
    init_db()
    log.info("database_ready")

    # 3. Ensure S3 bucket exists (auto-creates on MinIO; checks on real AWS)
    try:
        ensure_bucket_exists()
    except Exception as exc:
        log.error("s3_init_failed", error=str(exc))
        # Non-fatal on startup — uploads will fail individually if bucket is absent

    # 4. Create Qdrant collection if it does not exist
    init_collection()
    log.info("qdrant_ready", collection=settings.QDRANT_COLLECTION)

    # 5. Rebuild BM25 index from any existing documents in Qdrant
    texts, ids, metadatas = load_all_child_chunks()
    if texts:
        searcher.build_bm25_index(texts, ids, metadatas)
        log.info("bm25_index_ready", num_chunks=len(texts))
    else:
        log.info("bm25_index_empty", reason="no documents indexed yet")

    log.info("docflow_ready")
    yield

    log.info("docflow_shutdown")


app = FastAPI(
    title="Docflow",
    description=(
        "Multi-agent document processing and query API. "
        "Upload PDFs or images, then query them in natural language."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router, prefix="/api/v1", tags=["ingestion"])
app.include_router(query_router,  prefix="/api/v1", tags=["query"])
app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
app.include_router(chats_router, prefix="/api/v1", tags=["chats"])


@app.get("/health", tags=["ops"])
async def health_check():
    """Liveness probe used by Nginx and load balancers."""
    return {"status": "ok", "version": "1.0.0"}
