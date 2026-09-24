"""Application settings, loaded from environment variables (and `.env` when present)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_SUPPORTED_DB_SCHEMES = ("postgresql+asyncpg://", "sqlite+aiosqlite://")
_PLACEHOLDER_MARKER = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Application ---
    APP_NAME: str = "Recommendation Engine"
    APP_VERSION: str = "0.1.0"
    APP_ENV: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- PostgreSQL ---
    DATABASE_URL: str
    DB_POOL_SIZE: int = Field(default=10, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=20, ge=0)
    DB_ECHO: bool = False

    # --- Redis ---
    REDIS_URL: str

    # --- OpenAI embeddings ---
    OPENAI_API_KEY: SecretStr | None = None
    # OPENAI_EMBEDDING_MODEL is the name used before Module 2; still accepted.
    EMBEDDING_MODEL: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL"),
    )
    EMBEDDING_DIMENSION: int = Field(default=1536, gt=0)
    EMBEDDING_MAX_TOKENS: int = Field(default=8000, gt=0)
    EMBEDDING_CACHE_TTL_SECONDS: int = Field(default=24 * 60 * 60, gt=0)

    # --- Pinecone ---
    PINECONE_API_KEY: SecretStr | None = None
    # Region and cloud for serverless indexes, e.g. us-east-1 on aws.
    PINECONE_ENVIRONMENT: str = "us-east-1"
    PINECONE_CLOUD: Literal["aws", "gcp", "azure"] = "aws"

    # --- Ingestion ---
    MAX_ITEMS_PER_REQUEST: int = Field(default=1000, gt=0)
    MAX_SYNC_ITEMS: int = Field(default=50, gt=0)
    MAX_CSV_ITEMS: int = Field(default=10_000, gt=0)
    MAX_CSV_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)

    # --- Rate limiting ---
    RATE_LIMIT_RPM: int = Field(default=100, gt=0)
    DAILY_ITEM_LIMIT: int = Field(default=10_000, gt=0)

    # --- Embedding worker ---
    WORKER_POLL_INTERVAL_SECONDS: float = Field(default=2.0, gt=0)
    WORKER_CHUNK_SIZE: int = Field(default=100, gt=0)
    # Items stuck in PROCESSING longer than this (a crashed worker) are retried.
    WORKER_STALE_AFTER_SECONDS: int = Field(default=600, gt=0)

    # --- Security ---
    SECRET_KEY: SecretStr = Field(min_length=32)
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Operational ---
    HEALTH_CHECK_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0)
    # Port for the worker's Prometheus metrics; 0 disables them.
    WORKER_METRICS_PORT: int = Field(default=9100, ge=0, le=65535)
    # Error tracking. Leave empty to disable.
    SENTRY_DSN: SecretStr | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.0, ge=0, le=1)

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_DB_SCHEMES):
            raise ValueError(
                f"DATABASE_URL must use an async driver, one of: {', '.join(_SUPPORTED_DB_SCHEMES)}"
            )
        return value

    @field_validator("OPENAI_API_KEY", "PINECONE_API_KEY", "SENTRY_DSN", mode="before")
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        # `OPENAI_API_KEY=` in .env means "not configured", not an empty key.
        if isinstance(value, str) and (not value.strip() or _PLACEHOLDER_MARKER in value):
            return None
        return value

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _enforce_production_rules(self) -> "Settings":
        if not self.is_production:
            return self
        problems: list[str] = []
        if _PLACEHOLDER_MARKER in self.SECRET_KEY.get_secret_value():
            problems.append("SECRET_KEY is still a placeholder value")
        if not self.OPENAI_API_KEY:
            problems.append("OPENAI_API_KEY is required")
        if not self.PINECONE_API_KEY:
            problems.append("PINECONE_API_KEY is required")
        if "*" in self.ALLOWED_ORIGINS:
            problems.append("ALLOWED_ORIGINS must not contain '*'")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def docs_enabled(self) -> bool:
        return not self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment


settings = get_settings()
