import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class ItemStats(Base):
    """How people responded to one item recently, rebuilt by the worker from impressions
    and feedback. Counts are time-decayed (an event loses half its weight every
    ITEM_STATS_HALF_LIFE_DAYS), so they are floats. Items never shown have no row."""

    __tablename__ = "item_stats"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    external_item_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    impressions: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    clicks: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # THUMBS_UP.
    positives: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # THUMBS_DOWN and IGNORE.
    negatives: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # PURCHASE and APPLY.
    conversions: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return f"<ItemStats item={self.external_item_id!r} impressions={self.impressions:.1f}>"
