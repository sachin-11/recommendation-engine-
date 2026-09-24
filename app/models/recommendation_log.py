import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.item import JSONType


class QueryType(StrEnum):
    TEXT = "TEXT"
    ITEM_ID = "ITEM_ID"
    PROFILE = "PROFILE"


class RecommendationLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One served recommendation request. Its id is the `query_id` clients send feedback for."""

    __tablename__ = "recommendation_logs"
    __table_args__ = (
        Index("ix_recommendation_logs_tenant_id_created_at", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    query_type: Mapped[QueryType] = mapped_column(
        SAEnum(QueryType, name="query_type"), nullable=False
    )
    query_input: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False)
    top_result_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    filters_applied: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    # HIT, MISS, BYPASS (raw data requested) or PARTIAL (batch); for the cache hit rate.
    cache_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    def __repr__(self) -> str:
        return f"<RecommendationLog id={self.id} {self.query_type} results={self.results_count}>"
