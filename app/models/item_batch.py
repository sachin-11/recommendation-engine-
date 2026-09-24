import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class BatchStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    PARTIAL_FAIL = "PARTIAL_FAIL"


class ItemBatch(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A group of items submitted together for asynchronous embedding."""

    __tablename__ = "item_batches"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    total_items: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[BatchStatus] = mapped_column(
        SAEnum(BatchStatus, name="batch_status"), nullable=False, default=BatchStatus.PENDING
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_complete(self) -> bool:
        return self.status in (BatchStatus.DONE, BatchStatus.PARTIAL_FAIL)

    def __repr__(self) -> str:
        return f"<ItemBatch id={self.id} {self.status} {self.processed_items}/{self.total_items}>"
