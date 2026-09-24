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
    # USD per 1M tokens, for the cost estimate in analytics (text-embedding-3-small: 0.02).
    EMBEDDING_PRICE_PER_MILLION_TOKENS: float = Field(default=0.02, ge=0)

    # --- Pinecone ---
    PINECONE_API_KEY: SecretStr | None = None
    # One shared serverless index; each tenant gets its own namespace in it.
    PINECONE_INDEX_NAME: str = Field(default="reco-shared", pattern=r"^[a-z0-9-]{1,45}$")
    # Region and cloud for the serverless index, e.g. us-east-1 on aws.
    PINECONE_ENVIRONMENT: str = "us-east-1"
    PINECONE_CLOUD: Literal["aws", "gcp", "azure"] = "aws"

    # --- Ingestion ---
    MAX_ITEMS_PER_REQUEST: int = Field(default=1000, gt=0)
    MAX_SYNC_ITEMS: int = Field(default=50, gt=0)
    MAX_CSV_ITEMS: int = Field(default=10_000, gt=0)
    MAX_CSV_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)

    # --- Recommendations ---
    # Lower bounds (exclusive) for Excellent, Good and Fair labels; lower is Weak.
    # Calibrated on text-embedding-3-small, where short queries against item text score
    # about 0.45-0.75 for relevant items and below 0.35 for unrelated ones.
    SCORE_LABEL_THRESHOLDS: Annotated[tuple[float, float, float], NoDecode] = (0.65, 0.50, 0.35)

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
    # Operator key for /api/v1/tenants/*. Unset = those routes are disabled.
    ADMIN_API_KEY: SecretStr | None = None
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Operational ---
    HEALTH_CHECK_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0)
    # Port for the worker's Prometheus metrics; 0 disables them.
    WORKER_METRICS_PORT: int = Field(default=9100, ge=0, le=65535)
    # LangSmith tracing of embeddings, vector search and recommendations (optional).
    # The LANGCHAIN_* names used by older LangSmith setups are accepted too.
    LANGSMITH_TRACING: bool = Field(
        default=False, validation_alias=AliasChoices("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
    )
    LANGSMITH_API_KEY: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")
    )
    LANGSMITH_PROJECT: str = Field(
        default="recoengine",
        validation_alias=AliasChoices("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT"),
    )
    LANGSMITH_ENDPOINT: str | None = Field(
        default=None, validation_alias=AliasChoices("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")
    )
    # Keep query and item text out of traces (only timings and counts are sent).
    LANGSMITH_HIDE_INPUTS: bool = False
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

    @field_validator(
        "OPENAI_API_KEY",
        "PINECONE_API_KEY",
        "SENTRY_DSN",
        "ADMIN_API_KEY",
        "LANGSMITH_API_KEY",
        mode="before",
    )
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        # `OPENAI_API_KEY=` in .env means "not configured", not an empty key.
        if isinstance(value, str) and (not value.strip() or _PLACEHOLDER_MARKER in value):
            return None
        return value

    @field_validator("ADMIN_API_KEY")
    @classmethod
    def _admin_key_is_strong(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("ADMIN_API_KEY must be at least 32 characters")
        return value

    @field_validator("SCORE_LABEL_THRESHOLDS", mode="before")
    @classmethod
    def _parse_thresholds(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(float(part) for part in value.split(","))
        return value

    @field_validator("SCORE_LABEL_THRESHOLDS")
    @classmethod
    def _thresholds_descending(
        cls, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        if not all(-1 <= t <= 1 for t in value) or not value[0] > value[1] > value[2]:
            raise ValueError("SCORE_LABEL_THRESHOLDS must be three descending values in [-1, 1]")
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
