import hashlib
from typing import List, Optional, Tuple, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    ScoredPoint,
    Record,
    FilterSelector,
)
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Read client used by the FastAPI process (searches + retrieves).
# Worker processes create their own client instance inside upsert_chunks.
_client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)


def chunk_id_to_point_id(chunk_id: str) -> int:
    """
    Convert a chunk ID string to a deterministic 63-bit unsigned integer
    for use as the Qdrant point ID.

    WHY SHA-256 instead of Python's hash():
      Python randomises its hash seed per process (PYTHONHASHSEED is set to
      a random value at each interpreter startup). The worker and API run in
      separate Docker containers — separate Python processes — so:
        worker:  hash("ab12-p0") → integer X  (stored in Qdrant as point X)
        api:     hash("ab12-p0") → integer Y  (looks up point Y → not found!)
      SHA-256 is cryptographically deterministic across ALL processes and
      platforms, so both containers always compute the same integer ID.
    """
    digest = hashlib.sha256(chunk_id.encode("utf-8")).digest()
    # First 8 bytes = 64-bit int; >>1 guarantees positive (Qdrant requires unsigned)
    return int.from_bytes(digest[:8], byteorder="big") >> 1


def init_collection() -> None:
    """
    Create the Qdrant collection if it does not already exist.
    Called once at FastAPI startup (app/main.py lifespan).
    """
    existing = {c.name for c in _client.get_collections().collections}
    if settings.QDRANT_COLLECTION in existing:
        log.info("qdrant_collection_exists", collection=settings.QDRANT_COLLECTION)
        return

    _client.create_collection(
        collection_name=settings.QDRANT_COLLECTION,
        vectors_config=VectorParams(
            size=settings.EMBEDDING_DIMENSION,
            distance=Distance.COSINE,
        ),
    )
    log.info(
        "qdrant_collection_created",
        collection=settings.QDRANT_COLLECTION,
        dimension=settings.EMBEDDING_DIMENSION,
    )


def upsert_chunks(
    chunks: List[Dict[str, Any]],
    embeddings: List[Optional[List[float]]],
    job_id: str,
    user_id: str,
    file_id: str,
    filename: str,
) -> None:
    """
    Upsert all chunks (parent and child) for a single document into Qdrant.

    Parent chunks receive a zero vector so they can be retrieved by ID but
    will never appear in similarity search (search filter restricts to
    type=='child').

    Called by: app/agents/indexer.py
    """
    client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    zero_vec = [0.0] * settings.EMBEDDING_DIMENSION

    points = []
    for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        point_vector = vector if vector is not None else zero_vec
        payload = {
            "id":          chunk["id"],
            "job_id":      job_id,
            "user_id":     user_id,
            "file_id":     file_id,
            "text":        chunk["text"],
            "type":        chunk["type"],
            "parent_id":   chunk.get("parent_id"),
            "filename":    filename,
            "chunk_index": i,
        }
        points.append(
            PointStruct(
                id=chunk_id_to_point_id(chunk["id"]),
                vector=point_vector,
                payload=payload,
            )
        )

    client.upsert(collection_name=settings.QDRANT_COLLECTION, points=points)
    log.info("qdrant_upsert_ok", job_id=job_id, num_points=len(points))


def vector_search(
    query_vector: List[float],
    top_k: int,
    user_id_filter: str,
    job_id_filter: Optional[str] = None,
    file_id_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search for top_k child chunks most similar to query_vector.

    Returns list of dicts with keys: id, text, type, parent_id, filename, score.
    Called by: app/search/hybrid.py
    """
    must = [
        FieldCondition(key="type", match=MatchValue(value="child")),
        FieldCondition(key="user_id", match=MatchValue(value=user_id_filter)),
    ]
    if job_id_filter:
        must.append(
            FieldCondition(key="job_id", match=MatchValue(value=job_id_filter))
        )
    if file_id_filter:
        must.append(
            FieldCondition(key="file_id", match=MatchValue(value=file_id_filter))
        )

    hits: List[ScoredPoint] = _client.search(
        collection_name=settings.QDRANT_COLLECTION,
        query_vector=query_vector,
        limit=top_k,
        query_filter=Filter(must=must),
        with_payload=True,
    )
    return [
        {
            "id":        h.payload["id"],
            "user_id":   h.payload.get("user_id", ""),
            "file_id":   h.payload.get("file_id", ""),
            "job_id":    h.payload.get("job_id", ""),
            "text":      h.payload["text"],
            "type":      h.payload["type"],
            "parent_id": h.payload.get("parent_id"),
            "filename":  h.payload.get("filename", ""),
            "score":     h.score,
        }
        for h in hits
    ]


def retrieve_by_ids(chunk_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Retrieve parent chunks from Qdrant by their string chunk IDs.
    chunk_id_to_point_id() converts them to Qdrant integer IDs.

    Called by: app/search/hybrid.py after fusing child results.
    """
    if not chunk_ids:
        return []
    point_ids = [chunk_id_to_point_id(cid) for cid in chunk_ids]
    records: List[Record] = _client.retrieve(
        collection_name=settings.QDRANT_COLLECTION,
        ids=point_ids,
        with_payload=True,
        with_vectors=False,
    )
    return [
        {
            "id":        r.payload["id"],
            "user_id":   r.payload.get("user_id", ""),
            "file_id":   r.payload.get("file_id", ""),
            "job_id":    r.payload.get("job_id", ""),
            "text":      r.payload["text"],
            "type":      r.payload.get("type"),
            "parent_id": r.payload.get("parent_id"),
            "filename":  r.payload.get("filename", ""),
        }
        for r in records
        if r.payload
    ]


def load_all_child_chunks() -> Tuple[List[str], List[str], List[Dict[str, str]]]:
    """
    Scroll through all child chunks in Qdrant and return (texts, ids).
    Used to build/rebuild the in-memory BM25 index.

    Called by: app/main.py (startup), app/search/hybrid.py (auto-rebuild).
    """
    texts: List[str] = []
    ids:   List[str] = []
    metadatas: List[Dict[str, str]] = []
    child_filter = Filter(
        must=[FieldCondition(key="type", match=MatchValue(value="child"))]
    )
    offset = None

    while True:
        records, offset = _client.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            scroll_filter=child_filter,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for r in records:
            if r.payload:
                texts.append(r.payload.get("text", ""))
                ids.append(r.payload.get("id", ""))
                metadatas.append(
                    {
                        "user_id": r.payload.get("user_id", ""),
                        "file_id": r.payload.get("file_id", ""),
                        "job_id": r.payload.get("job_id", ""),
                    }
                )
        if offset is None:
            break

    log.info("qdrant_child_chunks_loaded", count=len(texts))
    return texts, ids, metadatas


def delete_file_points(user_id: str, file_id: str) -> None:
    """Delete every Qdrant point for a single user's uploaded file."""
    selector = FilterSelector(
        filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="file_id", match=MatchValue(value=file_id)),
            ]
        )
    )
    _client.delete(collection_name=settings.QDRANT_COLLECTION, points_selector=selector)
    log.info("qdrant_file_deleted", user_id=user_id, file_id=file_id)
