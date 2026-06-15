from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.api.routes.query import answer_query
from app.db import get_db, row_to_dict, utc_now
from app.models.schemas import ChatCreate, ChatDetail, ChatMessage, ChatRecord, QueryRequest, QueryResponse

router = APIRouter()


@router.get("/chats", response_model=list[ChatRecord])
async def list_chats(current_user: dict = Depends(get_current_user)) -> list[ChatRecord]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, title, created_at, updated_at FROM chats
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (current_user["id"],),
        ).fetchall()
    return [ChatRecord(**dict(row)) for row in rows]


@router.post("/chats", response_model=ChatRecord, status_code=201)
async def create_chat(
    payload: ChatCreate,
    current_user: dict = Depends(get_current_user),
) -> ChatRecord:
    chat_id = str(uuid.uuid4())
    now = utc_now()
    title = payload.title.strip() or "New chat"
    with get_db() as db:
        db.execute(
            "INSERT INTO chats (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, current_user["id"], title, now, now),
        )
        row = db.execute(
            "SELECT id, title, created_at, updated_at FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
    return ChatRecord(**dict(row))


@router.get("/chats/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: str, current_user: dict = Depends(get_current_user)) -> ChatDetail:
    with get_db() as db:
        chat = row_to_dict(
            db.execute(
                "SELECT id, title, created_at, updated_at FROM chats WHERE id = ? AND user_id = ?",
                (chat_id, current_user["id"]),
            ).fetchone()
        )
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found.")
        messages = db.execute(
            """
            SELECT id, role, content, created_at FROM messages
            WHERE chat_id = ? AND user_id = ?
            ORDER BY created_at ASC
            """,
            (chat_id, current_user["id"]),
        ).fetchall()
    return ChatDetail(**chat, messages=[ChatMessage(**dict(row)) for row in messages])


@router.post("/chats/{chat_id}/messages", response_model=QueryResponse)
async def send_chat_message(
    chat_id: str,
    payload: QueryRequest,
    current_user: dict = Depends(get_current_user),
) -> QueryResponse:
    with get_db() as db:
        chat = db.execute(
            "SELECT id FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, current_user["id"]),
        ).fetchone()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found.")

    request = payload.model_copy(update={"chat_id": chat_id})
    return answer_query(request, current_user, persist_chat=True)


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    with get_db() as db:
        result = db.execute(
            "DELETE FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, current_user["id"]),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Chat not found.")
    return {"ok": True}
