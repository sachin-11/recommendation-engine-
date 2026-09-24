import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseEntity


class UsageSource(StrEnum):
    INGEST = "INGEST"  # embedding uploaded items (uploads, CSV, rebuilds)
    QUERY = "QUERY"  # embedding recommendation queries and profiles


class TokenUsage(BaseEntity):
    """OpenAI embedding usage per tenant, UTC day, source and model (running totals)."""

    __tablename__ = "token_usage"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "day", "source", "model", name="uq_token_usage_tenant_day_source_model"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[UsageSource] = mapped_column(
        SAEnum(UsageSource, name="usage_source"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Texts sent for embedding, and how many of them were served from the Redis cache.
    texts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
