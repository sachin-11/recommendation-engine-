"""Billing: the workspace's plan, limits and usage."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.tenant import Plan


class AllowanceOut(BaseModel):
    max_items: int | None = Field(description="Items the workspace can hold; null is no cap.")
    monthly_queries: int | None = Field(description="Recommendations per month; null is no cap.")
    rate_limit_rpm: int = Field(description="Requests per minute per API key.")
    llm_features: bool = Field(description="LLM re-ranking and asking in plain language.")


class UsageOut(BaseModel):
    items: int
    queries_this_month: int


class PlanOut(BaseModel):
    plan: Plan
    allowance: AllowanceOut


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval: Literal["month", "year"] = "month"


class RedirectResponse(BaseModel):
    url: str = Field(description="A Stripe-hosted page to send the user to.")


class BillingResponse(BaseModel):
    enabled: bool = Field(description="False: billing is off and nothing is limited by plan.")
    plan: Plan = Field(description="The plan in force now.")
    subscribed_plan: Plan = Field(description="The plan paid for, which may have lapsed.")
    subscription_status: str | None = Field(
        description="Stripe's status: active, trialing, past_due, canceled, ..."
    )
    current_period_end: datetime | None
    cancel_at_period_end: bool
    allowance: AllowanceOut = Field(description="Limits in force, admin overrides included.")
    usage: UsageOut
    plans: list[PlanOut] = Field(description="What each plan includes.")
