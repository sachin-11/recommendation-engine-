"""Application settings, loaded from environment variables (and `.env` when present)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # --- AI / vector providers (consumed from Module 2 onwards) ---
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    PINECONE_API_KEY: SecretStr | None = None
    PINECONE_ENVIRONMENT: str | None = None

    # --- Security ---
    SECRET_KEY: SecretStr = Field(min_length=32)
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Operational ---
    HEALTH_CHECK_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0)

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if not value.startswith(_SUPPORTED_DB_SCHEMES):
            raise ValueError(
                f"DATABASE_URL must use an async driver, one of: {', '.join(_SUPPORTED_DB_SCHEMES)}"
            )
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
    return Settings()  # type: ignore[call-arg]  # values come from the environment


settings = get_settings()
