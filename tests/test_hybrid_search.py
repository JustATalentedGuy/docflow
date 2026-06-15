import pytest
from app.search.hybrid import HybridSearcher


# ── RRF fusion ────────────────────────────────────────────────────────────────

def test_rrf_document_in_both_lists_ranks_first():
    vector_hits = [
        {"id": "chunk-A", "parent_id": "parent-A"},
        {"id": "chunk-B", "parent_id": "parent-B"},
        {"id": "chunk-C", "parent_id": "parent-C"},
    ]
    bm25_hits = [
        {"id": "chunk-B", "parent_id": "parent-B"},
        {"id": "chunk-D", "parent_id": "parent-D"},
    ]
    fused = HybridSearcher._reciprocal_rank_fusion(vector_hits, bm25_hits, k=60)
    ids = [h["id"] for h in fused]
    # chunk-B appears in both lists → must have highest combined RRF score
    assert ids[0] == "chunk-B"


def test_rrf_scores_are_positive():
    hits = [{"id": "x", "parent_id": "px"}, {"id": "y", "parent_id": "py"}]
    fused = HybridSearcher._reciprocal_rank_fusion(hits, hits, k=60)
    for h in fused:
        assert h["rrf_score"] > 0


def test_rrf_empty_inputs():
    fused = HybridSearcher._reciprocal_rank_fusion([], [], k=60)
    assert fused == []


def test_rrf_one_empty_list():
    hits = [{"id": "a", "parent_id": "pa"}, {"id": "b", "parent_id": "pb"}]
    fused = HybridSearcher._reciprocal_rank_fusion(hits, [], k=60)
    assert len(fused) == 2
    ids = [h["id"] for h in fused]
    assert "a" in ids and "b" in ids


def test_rrf_higher_rank_means_higher_score():
    hits = [{"id": "first", "parent_id": "p1"}, {"id": "second", "parent_id": "p2"}]
    fused = HybridSearcher._reciprocal_rank_fusion(hits, [], k=60)
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]


# ── BM25 index ────────────────────────────────────────────────────────────────

def test_bm25_build_and_search():
    s = HybridSearcher()
    texts = [
        "The quick brown fox jumps over the lazy dog",
        "Machine learning is a subset of artificial intelligence",
        "Python is a popular programming language",
    ]
    ids = ["id-fox", "id-ml", "id-python"]
    s.build_bm25_index(texts, ids)

    results = s._bm25_search("fox jumps dog", top_k=3)
    assert len(results) > 0
    assert results[0]["id"] == "id-fox"


def test_bm25_returns_empty_before_index_built():
    s = HybridSearcher()
    results = s._bm25_search("any query", top_k=5)
    assert results == []


def test_bm25_irrelevant_query_returns_empty():
    s = HybridSearcher()
    s.build_bm25_index(
        ["The cat sat on the mat"], ["id-cat"]
    )
    # Words with zero BM25 score are filtered out
    results = s._bm25_search("xyzzy quantum frobnicate", top_k=5)
    assert results == []


def test_bm25_rebuild_replaces_old_index():
    """
    After rebuild, the old index is fully replaced.
    Needs ≥3 docs so the search term has a positive IDF score in BM25Okapi.
    (BM25Okapi IDF formula: log((N-n+0.5)/(n+0.5)); positive only when n < N/2)
    """
    s = HybridSearcher()
    s.build_bm25_index(["old document content"], ["old-id"])

    # New index: 3 docs, "feline" appears only in one
    s.build_bm25_index(
        [
            "new document content about feline cats",
            "other document about canine dogs",
            "third document about avian birds",
        ],
        ["new-id", "other-id", "third-id"],
    )

    results = s._bm25_search("feline cats", top_k=3)
    assert len(results) > 0, "Expected at least one BM25 result"
    assert results[0]["id"] == "new-id"


# ── Parent ID derivation ──────────────────────────────────────────────────────

def test_derive_parent_id_standard_format():
    s = HybridSearcher()
    assert s._derive_parent_id("ab12ef-p0-c3")   == "ab12ef-p0"
    assert s._derive_parent_id("ab12ef-p3-c0")   == "ab12ef-p3"
    assert s._derive_parent_id("ab12ef-p10-c99") == "ab12ef-p10"


def test_derive_parent_id_returns_none_for_parent():
    s = HybridSearcher()
    assert s._derive_parent_id("ab12ef-p0") is None
    assert s._derive_parent_id("ab12ef")    is None


def test_derive_parent_id_ignores_non_numeric_suffix():
    s = HybridSearcher()
    # "-c" followed by non-digits should not be treated as child marker
    assert s._derive_parent_id("chunk-concept-p0") is None
