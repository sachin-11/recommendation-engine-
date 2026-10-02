"""What each plan allows, and which plan a workspace is entitled to right now.

With BILLING_ENABLED off (the default: development, tests, self-hosting) every workspace
has the unlimited "self-hosted" allowance and every feature, as before billing existed.
With it on, the plan applies:

    plan       items    recommendations/month   LLM features (re-ranking, Ask)
    FREE       1,000    5,000                   no
    PRO        50,000   100,000                 yes

A limit a platform admin set on the workspace (Module 9) takes precedence over the plan's.
PRO counts only while its Stripe subscription is active, trialing or past_due; past_due
is a grace period while Stripe retries the payment. A platform admin can also give PRO
without a subscription, for a time or for good (complimentary Pro).
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from app.core.config import settings
from app.core.exceptions import PlanRequiredError
from app.models.base import utcnow
from app.models.tenant import Plan, Tenant

# Stripe subscription statuses in which a paid plan is in force.
PAID_STATUSES = frozenset({"active", "trialing", "past_due"})


@dataclass(frozen=True)
class Allowance:
    """A cap of None is no cap; rate_limit_rpm None is the platform default."""

    max_items: int | None
    monthly_queries: int | None
    rate_limit_rpm: int | None
    llm_features: bool


PLANS: dict[Plan, Allowance] = {
    Plan.FREE: Allowance(
        max_items=1_000, monthly_queries=5_000, rate_limit_rpm=None, llm_features=False
    ),
    Plan.PRO: Allowance(
        max_items=50_000, monthly_queries=100_000, rate_limit_rpm=None, llm_features=True
    ),
}
UNLIMITED = Allowance(max_items=None, monthly_queries=None, rate_limit_rpm=None, llm_features=True)


def is_complimentary(tenant: Tenant, now: datetime | None = None) -> bool:
    """Whether a platform admin's complimentary Pro is in force."""
    if tenant.comp_pro_since is None:
        return False
    until = tenant.comp_pro_until
    if until is None:
        return True
    # SQLite returns naive datetimes; everything is stored in UTC.
    return (until if until.tzinfo else until.replace(tzinfo=UTC)) > (now or utcnow())


def entitled_plan(tenant: Tenant) -> Plan:
    """The plan in force: complimentary Pro, else the paid plan while its subscription is
    in force; a paid plan whose subscription lapsed falls back to FREE."""
    if is_complimentary(tenant):
        return Plan.PRO
    if tenant.plan is Plan.FREE:
        return Plan.FREE
    return tenant.plan if tenant.subscription_status in PAID_STATUSES else Plan.FREE


def allowance(tenant: Tenant) -> Allowance:
    """The workspace's limits: an admin override where set, else its plan's."""
    base = PLANS[entitled_plan(tenant)] if settings.BILLING_ENABLED else UNLIMITED
    return replace(
        base,
        max_items=tenant.max_items if tenant.max_items is not None else base.max_items,
        monthly_queries=(
            tenant.monthly_query_limit
            if tenant.monthly_query_limit is not None
            else base.monthly_queries
        ),
        rate_limit_rpm=(
            tenant.rate_limit_rpm if tenant.rate_limit_rpm is not None else base.rate_limit_rpm
        ),
    )


def require_llm_features(tenant: Tenant, feature: str) -> None:
    if not allowance(tenant).llm_features:
        raise PlanRequiredError(f"{feature} is part of the Pro plan. Upgrade under Billing.")
