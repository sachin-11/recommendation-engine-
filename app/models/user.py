import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, true
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntity

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Role(StrEnum):
    """Workspace roles, from least to most privileged."""

    VIEWER = "VIEWER"  # read items and analytics, run recommendations
    DEVELOPER = "DEVELOPER"  # + upload and delete items, rebuild the index, manage API keys
    ADMIN = "ADMIN"  # + domain config and team members
    OWNER = "OWNER"  # + ownership transfer and account deletion; exactly one per workspace

    @property
    def rank(self) -> int:
        return list(Role).index(self)

    def at_least(self, other: "Role") -> bool:
        return self.rank >= other.rank


class User(BaseEntity):
    """A person who signs in to the dashboard. Belongs to exactly one workspace (tenant)."""

    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="user_role"), nullable=False)
    # Null until the user sets one (e.g. the owner of a tenant created by an operator).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship(lazy="raise")

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
