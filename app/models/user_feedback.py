import uuid
from enum import StrEnum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class FeedbackType(StrEnum):
    CLICK = "CLICK"
    THUMBS_UP = "THUMBS_UP"
    THUMBS_DOWN = "THUMBS_DOWN"
    PURCHASE = "PURCHASE"
    APPLY = "APPLY"
    IGNORE = "IGNORE"


class UserFeedback(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An end user's reaction to one recommended item. Collected now to train rankers later."""

    __tablename__ = "user_feedback"
    __table_args__ = (Index("ix_user_feedback_tenant_id_created_at", "tenant_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    recommendation_log_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    feedback_type: Mapped[FeedbackType] = mapped_column(
        SAEnum(FeedbackType, name="feedback_type"), nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserFeedback id={self.id} {self.feedback_type} item={self.external_item_id!r}>"
