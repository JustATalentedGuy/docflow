from pydantic import BaseModel, Field
from typing import List, Optional


class UserCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: str
    email: str
    created_at: str


class AuthResponse(BaseModel):
    token: str
    expires_at: str
    user: UserResponse


class UploadResponse(BaseModel):
    job_id: str = Field(..., description="UUID identifying this processing job")
    file_id: str = Field(..., description="UUID identifying the uploaded file")
    status: str = Field(..., description="Always 'queued' on successful acceptance")
    filename: str = Field(..., description="Original filename as uploaded")
    size_bytes: int = Field(..., description="File size in bytes")
    message: str = Field(..., description="Human-readable confirmation with poll URL")


class StatusResponse(BaseModel):
    job_id: str
    status: str = Field(
        ...,
        description=(
            "One of: 'queued' | 'processing' | 'completed' | 'failed:{reason}'. "
            "Poll until 'completed' before querying."
        ),
    )


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=1000,
                       description="Natural language question")
    job_id: Optional[str] = Field(
        None,
        description="Restrict search to a specific file processing job owned by the current user.",
    )
    file_id: Optional[str] = Field(
        None,
        description="Restrict search to a specific uploaded file owned by the current user.",
    )
    chat_id: Optional[str] = Field(
        None,
        description="Optional chat to append this exchange to and use as conversation history.",
    )
    top_k: int = Field(default=5, ge=1, le=20,
                       description="Number of parent chunks to pass to the LLM")


class SourceChunk(BaseModel):
    chunk_id: str
    text: str
    filename: str
    relevance_score: float


class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceChunk]
    query: str
    model: str
    chat_id: Optional[str] = None


class FileRecord(BaseModel):
    id: str
    job_id: str
    filename: str
    size_bytes: int
    content_type: str
    status: str
    created_at: str
    updated_at: str


class ChatCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=120)


class ChatRecord(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class ChatDetail(ChatRecord):
    messages: List[ChatMessage]
