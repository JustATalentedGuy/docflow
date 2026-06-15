from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.logging import get_logger
from app.db import get_db, row_to_dict, utc_now
from app.models.schemas import QueryRequest, QueryResponse, SourceChunk
from app.search.hybrid import searcher

router = APIRouter()
log = get_logger(__name__)

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        from langchain_groq import ChatGroq

        _llm = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            temperature=0.2,
            max_tokens=1024,
        )
    return _llm


_SYSTEM_PROMPT = (
    "You are a precise document assistant. "
    "Use only the current user's retrieved document context for factual answers. "
    "Use the conversation history only to understand follow-up references, never as a source. "
    "If the answer is not in the context, say: "
    "'I could not find this information in the provided documents.' "
    "If the user asks for an example and no example appears in the context, say that no source "
    "example was found before giving a clearly labeled general example. Be concise and direct."
)


def _get_chat_for_user(chat_id: str, user_id: str) -> dict | None:
    with get_db() as db:
        return row_to_dict(
            db.execute(
                "SELECT * FROM chats WHERE id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
        )


def _recent_chat_history(chat_id: str, user_id: str, limit: int = 12) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT role, content FROM messages
            WHERE chat_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (chat_id, user_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _save_message(chat_id: str, user_id: str, role: str, content: str) -> None:
    now = utc_now()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO messages (id, chat_id, user_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), chat_id, user_id, role, content, now),
        )
        db.execute(
            "UPDATE chats SET updated_at = ? WHERE id = ? AND user_id = ?",
            (now, chat_id, user_id),
        )


def answer_query(request: QueryRequest, current_user: dict, persist_chat: bool = True) -> QueryResponse:
    history: list[dict] = []
    if request.chat_id:
        if not _get_chat_for_user(request.chat_id, current_user["id"]):
            raise HTTPException(status_code=404, detail="Chat not found.")
        history = _recent_chat_history(request.chat_id, current_user["id"])

    retrieval_query = request.query
    if history:
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
        retrieval_query = f"{history_text}\nuser: {request.query}"

    results = searcher.search(
        query=retrieval_query,
        top_k=request.top_k,
        user_id_filter=current_user["id"],
        job_id_filter=request.job_id,
        file_id_filter=request.file_id,
    )

    if not results:
        raise HTTPException(
            status_code=503,
            detail="No indexed documents found for this user. Upload and process a document first.",
        )

    context = "\n\n---\n\n".join(
        f"[Source {i + 1} - {r['filename']}]\n{r['text']}" for i, r in enumerate(results)
    )
    history_block = "\n".join(f"{m['role'].title()}: {m['content']}" for m in history)
    if not history_block:
        history_block = "No prior messages."

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Conversation history:\n{history_block}\n\n"
                f"Retrieved document context:\n\n{context}\n\n"
                f"Current question: {request.query}"
            )
        ),
    ]

    try:
        response = _get_llm().invoke(messages)
        answer = response.content
    except Exception as exc:
        log.error("groq_call_failed", query=request.query[:80], error=str(exc))
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}")

    sources = [
        SourceChunk(
            chunk_id=r["id"],
            text=r["text"],
            filename=r["filename"],
            relevance_score=round(r["score"], 4),
        )
        for r in results
    ]

    if request.chat_id and persist_chat:
        _save_message(request.chat_id, current_user["id"], "user", request.query)
        _save_message(request.chat_id, current_user["id"], "assistant", answer)

    log.info(
        "query_answered",
        user_id=current_user["id"],
        chat_id=request.chat_id,
        query=request.query[:80],
        num_sources=len(sources),
    )
    return QueryResponse(
        answer=answer,
        sources=sources,
        query=request.query,
        model=settings.GROQ_MODEL,
        chat_id=request.chat_id,
    )


@router.post("/query", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    current_user: dict = Depends(get_current_user),
) -> QueryResponse:
    return answer_query(request, current_user, persist_chat=bool(request.chat_id))
