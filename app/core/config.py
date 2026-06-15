from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # AWS / MinIO
    AWS_ACCESS_KEY_ID: str = "minioadmin"
    AWS_SECRET_ACCESS_KEY: str = "minioadmin"
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "docflow-uploads"
    # Set to http://minio:9000 for local MinIO; leave empty for real AWS S3
    AWS_ENDPOINT_URL: Optional[str] = None

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Application database and auth
    DATABASE_URL: str = "sqlite:///data/docflow.db"
    SESSION_TTL_HOURS: int = 168

    # Qdrant
    QDRANT_HOST: str = "qdrant"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "docflow_chunks"

    # Groq LLM
    GROQ_API_KEY: str = "CHANGE_ME"
    GROQ_MODEL: str = "llama-3.1-8b-instant"

    # Hugging Face (BLIP-2 image captioning)
    HF_API_TOKEN: str = "CHANGE_ME"
    HF_CAPTIONING_MODEL: str = "Salesforce/blip-image-captioning-large"

    # LangSmith tracing (auto-read by LangChain if set)
    LANGCHAIN_API_KEY: str = "CHANGE_ME"
    LANGCHAIN_TRACING_V2: str = "true"
    LANGCHAIN_PROJECT: str = "docflow"

    # Embedding model (sentence-transformers)
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384

    # Logging
    LOG_LEVEL: str = "INFO"

    # Upload limits
    MAX_FILE_SIZE_MB: int = 50

    # Chunking strategy
    PARENT_CHUNK_SIZE: int = 1500
    PARENT_CHUNK_OVERLAP: int = 200
    CHILD_CHUNK_SIZE: int = 300
    CHILD_CHUNK_OVERLAP: int = 50

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


# Single application-wide instance — import this, not Settings
settings = Settings()
