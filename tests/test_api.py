from unittest.mock import patch, MagicMock
import pytest


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── POST /api/v1/upload ───────────────────────────────────────────────────────

def test_upload_rejects_unsupported_extension(client):
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("notes.docx", b"dummy content", "application/vnd.openxmlformats")},
    )
    assert resp.status_code == 415
    assert "Unsupported file type" in resp.json()["detail"]


def test_upload_rejects_oversized_file(client):
    big_content = b"x" * (51 * 1024 * 1024)  # 51 MB
    resp = client.post(
        "/api/v1/upload",
        files={"file": ("big.pdf", big_content, "application/pdf")},
    )
    assert resp.status_code == 413


def test_upload_accepts_pdf(client, sample_pdf_bytes):
    with patch("app.api.routes.upload.upload_to_s3") as mock_s3, \
         patch("app.api.routes.upload.process_document") as mock_task, \
         patch("app.api.deps._redis_pool"):

        mock_s3.return_value = "s3://docflow-uploads/uploads/abc/test.pdf"
        mock_task.delay = MagicMock()

        # Re-import with fresh Redis mock
        from redis import Redis
        with patch.object(Redis, "setex"):
            resp = client.post(
                "/api/v1/upload",
                files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
            )

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"
    assert data["filename"] == "test.pdf"
    assert data["size_bytes"] == len(sample_pdf_bytes)


def test_upload_accepts_png(client):
    # Minimal 1x1 white PNG
    png_bytes = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
        b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
        b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    with patch("app.api.routes.upload.upload_to_s3"), \
         patch("app.api.routes.upload.process_document") as mock_task, \
         patch("app.api.deps._redis_pool"):
        mock_task.delay = MagicMock()
        from redis import Redis
        with patch.object(Redis, "setex"):
            resp = client.post(
                "/api/v1/upload",
                files={"file": ("photo.png", png_bytes, "image/png")},
            )
    assert resp.status_code == 202


# ── GET /api/v1/status/{job_id} ───────────────────────────────────────────────

def test_status_not_found(client):
    from redis import Redis
    with patch.object(Redis, "get", return_value=None):
        resp = client.get("/api/v1/status/nonexistent-job-id-12345")
    assert resp.status_code == 404


def test_status_returns_queued(client):
    from redis import Redis
    from app.db import get_db

    with get_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO files
            (id, user_id, job_id, filename, s3_key, size_bytes, content_type, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "file-queued",
                "test-user-id",
                "some-valid-job-id",
                "queued.pdf",
                "uploads/test-user-id/some-valid-job-id/queued.pdf",
                123,
                "application/pdf",
                "queued",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    with patch.object(Redis, "get", return_value=b"queued"):
        resp = client.get("/api/v1/status/some-valid-job-id")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_status_returns_completed(client):
    from redis import Redis
    from app.db import get_db

    with get_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO files
            (id, user_id, job_id, filename, s3_key, size_bytes, content_type, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "file-completed",
                "test-user-id",
                "some-job-id",
                "done.pdf",
                "uploads/test-user-id/some-job-id/done.pdf",
                123,
                "application/pdf",
                "completed",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    with patch.object(Redis, "get", return_value=b"completed"):
        resp = client.get("/api/v1/status/some-job-id")
    assert resp.json()["status"] == "completed"
