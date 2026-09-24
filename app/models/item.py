import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseEntity

JSONType = JSONB().with_variant(JSON(), "sqlite")


class EmbeddingStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class Item(BaseEntity):
    """One tenant item (a job, dish, product...). `external_id` is the tenant's own ID."""

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_id", name="uq_items_tenant_id_external_id"),
        Index("ix_items_tenant_id_embedding_status", "tenant_id", "embedding_status"),
        Index("ix_items_embedding_status_updated_at", "embedding_status", "updated_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # The batch that last (re)submitted this item; drives batch progress.
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("item_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    embedding_status: Mapped[EmbeddingStatus] = mapped_column(
        SAEnum(EmbeddingStatus, name="embedding_status"),
        nullable=False,
        default=EmbeddingStatus.PENDING,
    )
    pinecone_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # `metadata` is reserved on declarative models, so the attribute is named differently.
    item_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONType, nullable=False, default=dict
    )

    def __repr__(self) -> str:
        return f"<Item id={self.id} external_id={self.external_id!r} {self.embedding_status}>"
