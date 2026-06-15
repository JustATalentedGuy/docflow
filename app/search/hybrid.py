from __future__ import annotations

import numpy as np
from typing import TYPE_CHECKING, List, Dict, Optional, Any
from rank_bm25 import BM25Okapi

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    # Deferred at runtime — only used for type hints in IDE
    from sentence_transformers import SentenceTransformer

log = get_logger(__name__)


class HybridSearcher:
    """
    Combines BM25 lexical search with Qdrant vector similarity search.
    Results are fused with Reciprocal Rank Fusion (RRF).

    Heavy dependencies (sentence_transformers, qdrant_client) are imported
    lazily inside the methods that need them. This keeps import time low and
    allows pure-logic tests to run without those packages installed.

    RRF formula: score(doc) = Σ_list  1 / (k + rank_in_list(doc))
    k=60 is the standard dampening constant.
    """

    def __init__(self) -> None:
        self._bm25: Optional[BM25Okapi] = None
        self._corpus_texts: List[str] = []
        self._corpus_ids:   List[str] = []
        self._corpus_metadatas: List[Dict[str, str]] = []
        self._embedder: Optional["SentenceTransformer"] = None  # lazy

    # ── Public API ────────────────────────────────────────────────────────────

    def build_bm25_index(
        self,
        texts: List[str],
        ids: List[str],
        metadatas: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """
        Rebuild the in-memory BM25 index.

        Args:
            texts: Child chunk text strings.
            ids:   Parallel chunk ID strings (from Qdrant payload["id"]).
        """
        tokenized          = [t.lower().split() for t in texts]
        self._bm25         = BM25Okapi(tokenized)
        self._corpus_texts = texts
        self._corpus_ids   = ids
        self._corpus_metadatas = metadatas or [{} for _ in ids]
        log.info("bm25_index_built", size=len(texts))

    def search(
        self,
        query: str,
        top_k: int = 5,
        user_id_filter: str = "",
        job_id_filter: Optional[str] = None,
        file_id_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run hybrid search and return the top_k most relevant PARENT chunks.

        Steps:
          1. Encode query with SentenceTransformer (lazy-loaded).
          2. Qdrant vector search → top_k*3 child chunks.
          3. BM25 search → top_k*3 child chunk IDs from in-memory index.
          4. RRF fusion of both ranked lists.
          5. Deduplicate to unique parent_ids; take top_k.
          6. Retrieve parent chunks from Qdrant by ID.
          7. Return parents ordered by their best child's RRF score.

        Returns:
            List of dicts (len ≤ top_k), sorted by relevance desc.
            Each dict: {id, text, filename, score}
        """
        # Deferred imports — only needed when actually searching
        from app.storage.qdrant import vector_search, retrieve_by_ids, load_all_child_chunks

        # ── Fix: BM25 auto-rebuild ─────────────────────────────────────────
        # The BM25 index lives in-memory inside each container. The worker
        # rebuilds its own copy after indexing, but the API container's copy
        # is only built at startup. If documents were indexed AFTER the API
        # started (normal workflow), the API's BM25 is stale/empty.
        # Solution: if BM25 is empty when a query arrives, rebuild it now
        # from Qdrant (single fast scroll). After the first query following
        # any indexing operation, BM25 stays warm for all subsequent queries.
        if self._bm25 is None:
            texts, ids, metadatas = load_all_child_chunks()
            if texts:
                self.build_bm25_index(texts, ids, metadatas)
                log.info("bm25_auto_rebuilt_on_query", num_chunks=len(texts))
        # ───────────────────────────────────────────────────────────────────

        query_vector = self._get_embedder().encode(
            query,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).tolist()

        vector_hits = vector_search(
            query_vector=query_vector,
            top_k=top_k * 3,
            user_id_filter=user_id_filter,
            job_id_filter=job_id_filter,
            file_id_filter=file_id_filter,
        )

        bm25_hits = self._bm25_search(
            query,
            top_k=top_k * 3,
            user_id_filter=user_id_filter,
            job_id_filter=job_id_filter,
            file_id_filter=file_id_filter,
        )
        fused     = self._reciprocal_rank_fusion(vector_hits, bm25_hits, k=60)

        # Collect unique parent_ids in RRF score order
        seen_parents:  List[str]         = []
        parent_scores: Dict[str, float]  = {}
        for hit in fused:
            pid = hit.get("parent_id")
            if pid and pid not in parent_scores:
                seen_parents.append(pid)
                parent_scores[pid] = hit["rrf_score"]
            if len(seen_parents) >= top_k:
                break

        if not seen_parents:
            log.warning("hybrid_search_no_results", query=query[:80])
            return []

        parents    = retrieve_by_ids(seen_parents)
        parent_map = {p["id"]: p for p in parents}

        results = []
        for pid in seen_parents:
            if pid in parent_map:
                p = parent_map[pid]
                results.append({
                    "id":       p["id"],
                    "text":     p["text"],
                    "filename": p.get("filename", ""),
                    "score":    parent_scores[pid],
                })

        log.info("hybrid_search_complete", query=query[:80], returned=len(results))
        return results

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_embedder(self) -> "SentenceTransformer":
        """Load the SentenceTransformer model on first call."""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(settings.EMBEDDING_MODEL)
        return self._embedder

    def _bm25_search(
        self,
        query: str,
        top_k: int,
        user_id_filter: str = "",
        job_id_filter: Optional[str] = None,
        file_id_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run BM25 on in-memory corpus. Returns [] if index not built yet."""
        if self._bm25 is None or not self._corpus_ids:
            return []

        scores      = self._bm25.get_scores(query.lower().split())
        top_indices = np.argsort(scores)[::-1]

        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                break
            metadata = self._corpus_metadatas[idx] if idx < len(self._corpus_metadatas) else {}
            if user_id_filter and metadata.get("user_id") != user_id_filter:
                continue
            if job_id_filter and metadata.get("job_id") != job_id_filter:
                continue
            if file_id_filter and metadata.get("file_id") != file_id_filter:
                continue
            chunk_id  = self._corpus_ids[idx]
            parent_id = self._derive_parent_id(chunk_id)
            results.append({
                "id":         chunk_id,
                "parent_id":  parent_id,
                "user_id":    metadata.get("user_id", ""),
                "file_id":    metadata.get("file_id", ""),
                "job_id":     metadata.get("job_id", ""),
                "bm25_score": float(scores[idx]),
            })
            if len(results) >= top_k:
                break
        return results

    @staticmethod
    def _derive_parent_id(child_id: str) -> Optional[str]:
        """
        Extract parent ID from a child chunk ID.

        Child format:  "{prefix}-p{n}-c{m}"  (suffix after last -c must be digits)
        Parent format: "{prefix}-p{n}"
        Returns None for parent IDs or unrecognised formats.
        """
        marker = "-c"
        pos = child_id.rfind(marker)
        if pos != -1:
            suffix = child_id[pos + len(marker):]
            if suffix.isdigit():
                return child_id[:pos]
        return None

    @staticmethod
    def _reciprocal_rank_fusion(
        vector_hits: List[Dict],
        bm25_hits:   List[Dict],
        k: int = 60,
    ) -> List[Dict]:
        """
        Fuse two ranked lists using Reciprocal Rank Fusion.

        For each list L: score(doc) += 1 / (k + rank_in_L(doc))
        Rank is 1-indexed. Scores are summed across lists.
        Returns merged list sorted by rrf_score descending,
        with "rrf_score" key added to each dict.
        """
        rrf_scores: Dict[str, float] = {}
        hit_data:   Dict[str, Dict]  = {}

        for rank, hit in enumerate(vector_hits, start=1):
            doc_id = hit["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in hit_data:
                hit_data[doc_id] = hit

        for rank, hit in enumerate(bm25_hits, start=1):
            doc_id = hit["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in hit_data:
                hit_data[doc_id] = hit

        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
        return [
            {**hit_data[doc_id], "rrf_score": rrf_scores[doc_id]}
            for doc_id in sorted_ids
        ]


# Module-level singleton shared by query.py and tasks.py
searcher = HybridSearcher()
