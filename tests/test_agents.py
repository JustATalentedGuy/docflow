import pytest


# ── Chunker tests ─────────────────────────────────────────────────────────────

def test_chunker_produces_parent_and_child_chunks():
    from app.agents.chunker import chunk_document

    state = {
        "job_id": "testjob-1234-5678",
        "raw_text": ("The quick brown fox jumped over the lazy dog. " * 60),
        "image_captions": [],
    }
    result = chunk_document(state)

    assert "error" not in result
    assert "chunks" in result

    chunks   = result["chunks"]
    parents  = [c for c in chunks if c["type"] == "parent"]
    children = [c for c in chunks if c["type"] == "child"]

    assert len(parents) >= 1
    assert len(children) >= 1


def test_chunker_child_parent_id_matches_parent():
    from app.agents.chunker import chunk_document

    state = {
        "job_id": "testjob-abcd-efgh",
        "raw_text": ("Alpha beta gamma delta. " * 100),
        "image_captions": [],
    }
    result   = chunk_document(state)
    chunks   = result["chunks"]
    parent_ids = {c["id"] for c in chunks if c["type"] == "parent"}

    for child in [c for c in chunks if c["type"] == "child"]:
        assert child["parent_id"] is not None
        assert child["parent_id"] in parent_ids


def test_chunker_uses_captions_when_no_text():
    from app.agents.chunker import chunk_document

    state = {
        "job_id": "testjob-img-only",
        "raw_text": None,
        "image_captions": [
            {"page": 1, "image_index": 0,
             "caption": "A photograph of a mountain range at sunset. " * 30},
        ],
    }
    result = chunk_document(state)

    assert "error" not in result
    assert len(result["chunks"]) > 0
    # The caption text should appear in at least one chunk
    all_text = " ".join(c["text"] for c in result["chunks"])
    assert "mountain" in all_text.lower()


def test_chunker_returns_error_when_empty():
    from app.agents.chunker import chunk_document

    state = {
        "job_id": "testjob-empty",
        "raw_text": None,
        "image_captions": [],
    }
    result = chunk_document(state)
    assert "error" in result
    assert "chunks" not in result


def test_slide_text_cleaner_removes_repeated_presentation_chrome():
    from app.agents.text_cleaner import clean_document_pages

    pages = [
        """
        Pumping Lemma for Regular Languages
        Theory of Computation
        Dhannya SM
        Dhannya SM
        Regular Languages
        1 / 6
        Proving languages not to be regular
        • Not every language is a regular language.
        Dhannya SM
        Regular Languages
        2 / 6
        """,
        """
        Pumping Lemma
        Let L be a regular language.
        Theorem
        Dhannya SM
        Regular Languages
        4 / 6
        Proof of example
        • Let w = 0p1p ∈L.
        • Repeat y.
        Dhannya SM
        Regular Languages
        5 / 6
        """,
    ]

    cleaned = clean_document_pages(pages)

    assert "Dhannya SM" not in cleaned
    assert "Regular Languages" not in cleaned
    assert "1 / 6" not in cleaned
    assert "Not every language is a regular language" in cleaned
    assert "Proof of example" in cleaned
    assert "Let w = 0p1p" in cleaned


def test_chunker_parent_ids_never_have_parent_id():
    from app.agents.chunker import chunk_document

    state = {
        "job_id": "testjob-check-parents",
        "raw_text": "Sentence one. Sentence two. Sentence three. " * 40,
        "image_captions": [],
    }
    result  = chunk_document(state)
    parents = [c for c in result["chunks"] if c["type"] == "parent"]
    for p in parents:
        assert p["parent_id"] is None


# ── Embedder tests ────────────────────────────────────────────────────────────

def test_embedder_output_shape():
    pytest.importorskip("sentence_transformers",
                        reason="sentence-transformers not installed — runs inside Docker")
    from app.agents.embedder import embed_chunks

    state = {
        "job_id": "testjob-embed",
        "chunks": [
            {"id": "ab12-p0",    "text": "Parent context text goes here.",
             "type": "parent", "parent_id": None},
            {"id": "ab12-p0-c0", "text": "First child chunk text.",
             "type": "child",  "parent_id": "ab12-p0"},
            {"id": "ab12-p0-c1", "text": "Second child chunk text.",
             "type": "child",  "parent_id": "ab12-p0"},
        ],
    }
    result = embed_chunks(state)

    assert "error" not in result
    embs = result["embeddings"]
    assert len(embs) == 3
    assert embs[0] is None                       # parent: no embedding
    assert isinstance(embs[1], list)
    assert isinstance(embs[2], list)
    assert len(embs[1]) == 384                   # all-MiniLM-L6-v2 dimension
    assert len(embs[2]) == 384


def test_embedder_vectors_are_normalised():
    """Embeddings should be unit vectors (cosine similarity ready)."""
    import math
    pytest.importorskip("sentence_transformers",
                        reason="sentence-transformers not installed — runs inside Docker")
    from app.agents.embedder import embed_chunks

    state = {
        "job_id": "testjob-norm",
        "chunks": [
            {"id": "xx-p0-c0", "text": "Normalisation test sentence.",
             "type": "child", "parent_id": "xx-p0"},
        ],
    }
    result = embed_chunks(state)
    vec    = result["embeddings"][0]
    norm   = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-5   # unit vector within floating-point tolerance


def test_embedder_no_chunks_returns_error():
    pytest.importorskip("sentence_transformers",
                        reason="sentence-transformers not installed — runs inside Docker")
    from app.agents.embedder import embed_chunks

    result = embed_chunks({"job_id": "testjob", "chunks": []})
    assert "error" in result


def test_embedder_only_parent_chunks_returns_error():
    pytest.importorskip("sentence_transformers",
                        reason="sentence-transformers not installed — runs inside Docker")
    from app.agents.embedder import embed_chunks

    state = {
        "job_id": "testjob-parent-only",
        "chunks": [
            {"id": "ab-p0", "text": "Parent only.", "type": "parent", "parent_id": None},
        ],
    }
    result = embed_chunks(state)
    assert "error" in result


# ── Classifier tests (mocked S3) ──────────────────────────────────────────────

def test_classifier_detects_text_pdf(sample_pdf_bytes):
    from unittest.mock import patch
    from app.agents.classifier import classify_document

    with patch("app.agents.classifier.download_from_s3", return_value=sample_pdf_bytes):
        result = classify_document({
            "job_id": "test-cls",
            "s3_key": "uploads/test/doc.pdf",
            "filename": "doc.pdf",
        })

    assert result.get("doc_type") == "pdf_text"
    assert "raw_text" in result
    assert isinstance(result["raw_text"], str)


def test_classifier_detects_image():
    from unittest.mock import patch
    from app.agents.classifier import classify_document

    dummy_png = b"FAKE_PNG_BYTES"
    with patch("app.agents.classifier.download_from_s3", return_value=dummy_png):
        result = classify_document({
            "job_id": "test-img",
            "s3_key": "uploads/test/photo.png",
            "filename": "photo.png",
        })
    assert result.get("doc_type") == "image"


def test_classifier_unsupported_extension():
    from unittest.mock import patch
    from app.agents.classifier import classify_document

    with patch("app.agents.classifier.download_from_s3", return_value=b"content"):
        result = classify_document({
            "job_id": "test-unknown",
            "s3_key": "uploads/test/file.csv",
            "filename": "file.csv",
        })
    assert "error" in result
