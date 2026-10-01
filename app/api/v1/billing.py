"""Billing: the workspace's plan, limits and usage; Stripe checkout and webhooks."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.core.config import settings
from app.core.database import get_db
from app.middleware.auth import AuthDep
from app.models.item import Item
from app.models.recommendation_log import RecommendationLog
from app.models.tenant import Plan
from app.schemas.billing import AllowanceOut, BillingResponse, PlanOut, UsageOut
from app.services.billing.plans import PLANS, Allowance, allowance, entitled_plan
from app.services.workspace_limits import month_start

router = APIRouter(prefix="/billing", tags=["billing"], responses=AUTH_RESPONSES)


def _allowance_out(a: Allowance) -> AllowanceOut:
    return AllowanceOut(
        max_items=a.max_items,
        monthly_queries=a.monthly_queries,
        rate_limit_rpm=a.rate_limit_rpm or settings.RATE_LIMIT_RPM,
        llm_features=a.llm_features,
    )


@router.get("", dependencies=PROTECTED, summary="The workspace's plan, limits and usage")
async def get_billing(
    auth: AuthDep, session: Annotated[AsyncSession, Depends(get_db)]
) -> BillingResponse:
    tenant = auth.tenant
    items = await session.scalar(
        select(func.count()).select_from(Item).where(Item.tenant_id == tenant.id)
    )
    queries = await session.scalar(
        select(func.count()).where(
            RecommendationLog.tenant_id == tenant.id,
            RecommendationLog.created_at >= month_start(),
        )
    )
    return BillingResponse(
        enabled=settings.BILLING_ENABLED,
        plan=entitled_plan(tenant) if settings.BILLING_ENABLED else tenant.plan,
        subscribed_plan=tenant.plan,
        subscription_status=tenant.subscription_status,
        current_period_end=tenant.current_period_end,
        cancel_at_period_end=tenant.cancel_at_period_end,
        allowance=_allowance_out(allowance(tenant)),
        usage=UsageOut(items=items or 0, queries_this_month=queries or 0),
        plans=[PlanOut(plan=p, allowance=_allowance_out(PLANS[p])) for p in Plan],
    )
