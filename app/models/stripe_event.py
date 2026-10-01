from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class StripeEvent(CreatedAtMixin, Base):
    """A Stripe webhook event already handled. Stripe delivers at least once, so the same
    event can arrive twice; its id here means the second delivery is skipped."""

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    # What handling it did: "synced", "ignored", ...
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)

    def __repr__(self) -> str:
        return f"<StripeEvent {self.id} {self.type} {self.outcome}>"
