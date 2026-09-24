import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class ApiKey(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A tenant API key. Only the keyed hash is stored; the plain key is shown once."""

    __tablename__ = "api_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short-lived keys issued by dashboard login; hidden from the API key list.
    is_session: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="api_keys", lazy="raise")

    def __repr__(self) -> str:
        return f"<ApiKey id={self.id} prefix={self.key_prefix!r} active={self.is_active}>"
