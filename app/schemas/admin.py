"""Platform admin area: every workspace, its usage and limits."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.item import EmbeddingStatus
from app.models.tenant import DomainType, Plan
from app.schemas.account import UserResponse
from app.schemas.tenant import ApiKeyResponse

NonBlankReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
Limit = Annotated[int, Field(ge=1, le=1_000_000_000)]


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class WorkspaceSort(StrEnum):
    NEWEST = "newest"
    NAME = "name"
    QUERIES = "queries"
    TOKENS = "tokens"
    ITEMS = "items"
    LAST_ACTIVE = "last_active"


class WorkspaceLimitsOut(BaseModel):
    max_items: int | None = Field(description="Null: no cap.")
    monthly_query_limit: int | None = Field(description="Null: no cap.")
    rate_limit_rpm: int | None = Field(description="Null: the platform default (RATE_LIMIT_RPM).")


class WorkspaceBillingOut(BaseModel):
    plan: Plan = Field(description="The plan in force now.")
    subscribed_plan: Plan = Field(description="The plan paid for, which may have lapsed.")
    subscription_status: str | None = Field(
        description="Stripe's status: active, trialing, past_due, canceled, ..."
    )
    current_period_end: datetime | None
    cancel_at_period_end: bool
    stripe_customer_url: str | None = Field(
        description="The customer in Stripe's dashboard; null before a first checkout."
    )
    complimentary: bool = Field(description="Pro given by a platform admin, in force now.")
    complimentary_since: datetime | None
    complimentary_until: datetime | None = Field(description="Null with complimentary: no end.")
    complimentary_reason: str | None = Field(
        description="Internal note; not shown to the workspace."
    )


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    domain_type: DomainType
    status: WorkspaceStatus
    created_at: datetime
    owner_name: str | None
    members: int
    items: int
    queries_this_month: int
    tokens_this_month: int
    estimated_cost_this_month_usd: float
    last_active_at: datetime | None = Field(description="Most recent use of any of its API keys.")
    limits: WorkspaceLimitsOut
    billing: WorkspaceBillingOut


class WorkspaceList(BaseModel):
    workspaces: list[WorkspaceSummary]
    total: int
    page: int
    page_size: int
    pages: int


class DailyCount(BaseModel):
    date: str
    count: int


class WorkspaceDetail(WorkspaceSummary):
    suspended_at: datetime | None
    suspended_reason: str | None = Field(description="Internal note; not shown to the workspace.")
    email_verified: bool
    embedding_status: dict[EmbeddingStatus, int]
    queries_daily: list[DailyCount] = Field(description="Last 30 UTC days, oldest first.")
    tokens_last_30_days: int
    member_list: list[UserResponse]
    api_keys: list[ApiKeyResponse]


class TopWorkspace(BaseModel):
    id: uuid.UUID
    name: str
    queries_this_month: int
    tokens_this_month: int


class PlatformOverview(BaseModel):
    workspaces_total: int
    workspaces_active: int
    workspaces_suspended: int
    workspaces_new_last_30_days: int
    users_total: int
    items_total: int
    queries_today: int
    queries_this_month: int
    tokens_this_month: int
    estimated_cost_this_month_usd: float
    pro_paying: int = Field(description="Workspaces whose Pro subscription is in force.")
    pro_complimentary: int = Field(description="Workspaces with complimentary Pro now.")
    payments_past_due: int = Field(description="Subscriptions whose last payment failed.")
    queries_daily: list[DailyCount] = Field(description="All workspaces, last 30 UTC days.")
    top_workspaces: list[TopWorkspace] = Field(description="By queries this month.")


class SuspendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: NonBlankReason = Field(
        description="Why, for other admins (e.g. 'Invoice 42 unpaid'). Not shown to the workspace."
    )


class ComplimentaryGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: Annotated[int, Field(ge=1, le=3650)] | None = Field(
        default=None, description="How long it lasts; null: until revoked."
    )
    reason: NonBlankReason = Field(
        description="Why, for other admins (e.g. 'Demo for Acme'). Not shown to the workspace."
    )


class LimitsUpdate(BaseModel):
    """Only the fields sent are changed; send null to go back to the default."""

    model_config = ConfigDict(extra="forbid")

    max_items: Limit | None = None
    monthly_query_limit: Limit | None = None
    rate_limit_rpm: Annotated[int, Field(ge=1, le=100_000)] | None = None
