from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.db import create_session, get_db, hash_password, row_to_dict, verify_password, utc_now
from app.models.schemas import AuthResponse, UserCreate, UserLogin, UserResponse

router = APIRouter()


def _auth_response(user: dict) -> AuthResponse:
    with get_db() as db:
        token, expires_at = create_session(db, user["id"])
    return AuthResponse(
        token=token,
        expires_at=expires_at,
        user=UserResponse(id=user["id"], email=user["email"], created_at=user["created_at"]),
    )


@router.post("/auth/register", response_model=AuthResponse, status_code=201)
async def register(payload: UserCreate) -> AuthResponse:
    email = payload.email.strip().lower()
    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="A user with this email already exists.")

        user_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email, hash_password(payload.password), utc_now()),
        )
        user = row_to_dict(db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    return _auth_response(user)


@router.post("/auth/login", response_model=AuthResponse)
async def login(payload: UserLogin) -> AuthResponse:
    email = payload.email.strip().lower()
    with get_db() as db:
        user = row_to_dict(db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())

    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    return _auth_response(user)


@router.get("/auth/me", response_model=UserResponse)
async def me(current_user: dict = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        created_at=current_user["created_at"],
    )


@router.post("/auth/logout")
async def logout(current_user: dict = Depends(get_current_user)) -> dict:
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (current_user["id"],))
    return {"ok": True}
