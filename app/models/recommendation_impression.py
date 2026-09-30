import uuid

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class RecommendationImpression(CreatedAtMixin, Base):
    """One result shown in one served recommendation, at its rank. With feedback it gives
    each item's click-through rate: feedback over the times the item was shown."""

    __tablename__ = "recommendation_impressions"
    __table_args__ = (
        Index("ix_recommendation_impressions_tenant_id_created_at", "tenant_id", "created_at"),
        Index(
            "ix_recommendation_impressions_tenant_id_external_item_id",
            "tenant_id",
            "external_item_id",
        ),
    )

    recommendation_log_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_logs.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    external_item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:
        return f"<RecommendationImpression #{self.rank} item={self.external_item_id!r}>"
