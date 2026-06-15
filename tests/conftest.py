"""
Test configuration.

Stubs out heavy packages that are not installed in the lightweight CI/test
environment. All app modules are importable without Docker or cloud accounts.

Tests that exercise the real models (embedder shape, LLM output) use
pytest.importorskip to skip gracefully when the package is absent.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── Register lightweight MagicMock stubs for uninstalled packages ─────────────
# Order matters: parent packages before sub-packages.
_STUBS = [
    # langchain_core must NOT be here — langchain_text_splitters needs it as a real package
    "langchain_groq",
    "langgraph",
    "langgraph.graph",
    "celery",
    "celery.signals",
    "qdrant_client",
    "qdrant_client.models",
    # sentence_transformers must NOT be here — importorskip in embedder tests detects it
    "paddleocr",
    "paddle",
]
for _pkg in _STUBS:
    if _pkg not in sys.modules:
        sys.modules[_pkg] = MagicMock()

# Use the real message classes when LangChain is installed. The fallback keeps
# the lightweight test environment importable without replacing real modules.
try:
    import langchain_core.messages  # noqa: F401
except ImportError:
    _lc_msgs = MagicMock()
    _lc_msgs.SystemMessage = type("SystemMessage", (), {"__init__": lambda s, content: None})
    _lc_msgs.HumanMessage = type("HumanMessage", (), {"__init__": lambda s, content: None})
    sys.modules["langchain_core.messages"] = _lc_msgs

# langgraph.graph: END must be a real string constant.
import langgraph.graph as _lg  # noqa: E402 (already mocked above)
_lg.END         = "__end__"
_lg.StateGraph  = MagicMock()

# ── FastAPI test client (installed via uvicorn[standard]) ─────────────────────
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """
    FastAPI TestClient with all external services mocked out.
    Safe to run without Docker, AWS, or any heavy packages installed.
    """
    import app.main as main_mod
    from app.api.deps import get_current_user

    with patch.object(main_mod, "init_collection",        return_value=None), \
         patch.object(main_mod, "load_all_child_chunks",  return_value=([], [], [])), \
         patch.object(main_mod, "ensure_bucket_exists",   return_value=None), \
         patch.object(main_mod, "searcher",               new=MagicMock()):
        main_mod.app.dependency_overrides[get_current_user] = lambda: {
            "id": "test-user-id",
            "email": "test@example.com",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        with TestClient(main_mod.app) as c:
            from app.db import get_db, hash_password

            with get_db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (
                        "test-user-id",
                        "test@example.com",
                        hash_password("password123"),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
            yield c
        main_mod.app.dependency_overrides.clear()


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Minimal valid single-page text PDF with real extractable content."""
    try:
        import fitz
        doc  = fitz.open()
        page = doc.new_page()
        page.insert_text(
            (72, 72),
            "Docflow test document.\n\n"
            "This file contains sample content for automated testing. " * 20,
        )
        return doc.tobytes()
    except ImportError:
        # Fallback: minimal valid PDF (used for type/size validation only)
        return (
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF"
        )
