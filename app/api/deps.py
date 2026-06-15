from typing import Generator
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis, ConnectionPool
from app.core.config import settings
from app.db import get_db, get_user_by_token, row_to_dict

# Shared connection pool — created once at module import, reused across requests
_redis_pool = ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=10,
    decode_responses=False,
)

bearer_scheme = HTTPBearer(auto_error=False)


def get_redis() -> Generator[Redis, None, None]:
    """
    FastAPI dependency that yields a Redis client from the shared pool.

    Usage in route handlers:
        async def route(redis: Redis = Depends(get_redis)):
    """
    client = Redis(connection_pool=_redis_pool)
    try:
        yield client
    finally:
        client.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
        )

    with get_db() as db:
        user = get_user_by_token(db, credentials.credentials)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session.",
            )
        return row_to_dict(user)
