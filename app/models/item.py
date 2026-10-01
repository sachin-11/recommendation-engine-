import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    ColumnElement,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    literal_column,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseEntity

JSONType = JSONB().with_variant(JSON(), "sqlite")

# Full-text search config. 'simple' lowercases and splits words but does not stem or drop
# stop words: items are in any language, and meaning is the vector search's job; keywords
# are for exact terms (product codes, names, skills).
TS_CONFIG = "simple"


def search_vector(column: Any) -> ColumnElement[Any]:
    """The tsvector expression keyword search queries; the GIN index is on exactly this.
    Literals, not bind parameters: Postgres only uses an expression index when the query's
    expression is identical, and a parameter is not."""
    return func.to_tsvector(
        literal_column(f"'{TS_CONFIG}'"), func.coalesce(column, literal_column("''"))
    )


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
    # Searchable field values for keyword search, set when the item is embedded.
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Item id={self.id} external_id={self.external_id!r} {self.embedding_status}>"


# Postgres only: SQLite (tests) has no to_tsvector and searches in Python instead.
Index(
    "ix_items_search_vector",
    search_vector(Item.__table__.c.search_text),
    postgresql_using="gin",
).ddl_if(dialect="postgresql")
