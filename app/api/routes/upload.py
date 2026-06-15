import uuid
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from redis import Redis

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import FileRecord, UploadResponse, StatusResponse
from app.api.deps import get_current_user, get_redis
from app.db import get_db, row_to_dict, utc_now
from app.storage.qdrant import delete_file_points
from app.storage.s3 import delete_from_s3, upload_to_s3
from app.workers.tasks import process_document

router = APIRouter()
log = get_logger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff"}
MAX_SIZE_BYTES = settings.MAX_FILE_SIZE_MB * 1024 * 1024


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    redis: Redis = Depends(get_redis),
    current_user: dict = Depends(get_current_user),
) -> UploadResponse:
    """
    Accept a document for async processing.

    Returns job_id immediately (HTTP 202). Poll GET /api/v1/status/{job_id}
    until status == 'completed', then use POST /api/v1/query.
    """
    filename = file.filename or "unknown"
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content):,} bytes). Max: {settings.MAX_FILE_SIZE_MB} MB",
        )

    job_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    s3_key = f"uploads/{current_user['id']}/{job_id}/{filename}"

    upload_to_s3(
        content=content,
        key=s3_key,
        content_type=file.content_type or "application/octet-stream",
    )

    now = utc_now()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO files
            (id, user_id, job_id, filename, s3_key, size_bytes, content_type, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                current_user["id"],
                job_id,
                filename,
                s3_key,
                len(content),
                file.content_type or "application/octet-stream",
                "queued",
                now,
                now,
            ),
        )

    redis.setex(f"job:{job_id}", 86400, "queued")
    process_document.delay(job_id, current_user["id"], file_id, s3_key, filename)

    log.info(
        "document_accepted",
        job_id=job_id,
        user_id=current_user["id"],
        file_id=file_id,
        filename=filename,
        size_bytes=len(content),
    )

    return UploadResponse(
        job_id=job_id,
        file_id=file_id,
        status="queued",
        filename=filename,
        size_bytes=len(content),
        message=f"Accepted. Poll /api/v1/status/{job_id} until status is 'completed'.",
    )


@router.get("/status/{job_id}", response_model=StatusResponse)
async def get_job_status(
    job_id: str,
    redis: Redis = Depends(get_redis),
    current_user: dict = Depends(get_current_user),
) -> StatusResponse:
    """Return current processing status for a job."""
    with get_db() as db:
        file_row = db.execute(
            "SELECT id FROM files WHERE job_id = ? AND user_id = ?",
            (job_id, current_user["id"]),
        ).fetchone()
        if not file_row:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    raw = redis.get(f"job:{job_id}")
    if raw is None:
        with get_db() as db:
            file_row = db.execute(
                "SELECT status FROM files WHERE job_id = ? AND user_id = ?",
                (job_id, current_user["id"]),
            ).fetchone()
        return StatusResponse(job_id=job_id, status=file_row["status"])

    status_value = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return StatusResponse(job_id=job_id, status=status_value)


@router.get("/files", response_model=list[FileRecord])
async def list_files(current_user: dict = Depends(get_current_user)) -> list[FileRecord]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, job_id, filename, size_bytes, content_type, status, created_at, updated_at
            FROM files
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (current_user["id"],),
        ).fetchall()
    return [FileRecord(**dict(row)) for row in rows]


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    with get_db() as db:
        file_row = row_to_dict(
            db.execute(
                "SELECT * FROM files WHERE id = ? AND user_id = ?",
                (file_id, current_user["id"]),
            ).fetchone()
        )
        if not file_row:
            raise HTTPException(status_code=404, detail="File not found.")

    delete_file_points(current_user["id"], file_id)
    delete_from_s3(file_row["s3_key"])

    with get_db() as db:
        db.execute("DELETE FROM files WHERE id = ? AND user_id = ?", (file_id, current_user["id"]))

    log.info("file_deleted", user_id=current_user["id"], file_id=file_id)
    return {"ok": True}
