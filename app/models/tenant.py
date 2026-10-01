from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, false, true
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseEntity

if TYPE_CHECKING:
    from app.models.api_key import ApiKey


class Plan(StrEnum):
    FREE = "FREE"
    PRO = "PRO"


class DomainType(StrEnum):
    HR = "HR"
    FOOD = "FOOD"
    ECOMMERCE = "ECOMMERCE"
    EDTECH = "EDTECH"
    CUSTOM = "CUSTOM"


class Tenant(BaseEntity):
    """A workspace (business account). People sign in as its users; `domain_config`
    describes the shape of its items."""

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
    # The owner's address (kept in step with the OWNER user). Set when the owner verifies it.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set by a platform admin together with is_active=False; cleared on reactivation.
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Per-workspace limits set by a platform admin. Null means the plan's limit applies
    # (with billing off: no cap for items and monthly queries, RATE_LIMIT_RPM for requests).
    max_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_query_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Billing (Stripe). `plan` is what the workspace pays for; whether it is in force
    # depends on `subscription_status` (see app/services/billing/plans.py).
    plan: Mapped[Plan] = mapped_column(
        SAEnum(Plan, name="billing_plan"), nullable=False, default=Plan.FREE, server_default="FREE"
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Stripe's subscription status: active, trialing, past_due, canceled, unpaid, ...
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} email={self.email!r}>"
