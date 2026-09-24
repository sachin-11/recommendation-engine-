from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, String, true
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntity

if TYPE_CHECKING:
    from app.models.api_key import ApiKey


class DomainType(StrEnum):
    HR = "HR"
    FOOD = "FOOD"
    ECOMMERCE = "ECOMMERCE"
    EDTECH = "EDTECH"
    CUSTOM = "CUSTOM"


class Tenant(BaseEntity):
    """A business account. `domain_config` describes the shape of the tenant's items."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    domain_type: Mapped[DomainType] = mapped_column(
        SAEnum(DomainType, name="domain_type"), nullable=False
    )
    domain_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} email={self.email!r}>"
