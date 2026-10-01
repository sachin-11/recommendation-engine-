"""Billing: the workspace's plan, limits and usage; Stripe checkout and webhooks."""

import json
import logging
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.core.redis_client import get_redis
from app.middleware.auth import AuthDep, require_role
from app.models.item import Item
from app.models.recommendation_log import RecommendationLog
from app.models.tenant import Plan
from app.models.user import Role
from app.schemas.billing import (
    AllowanceOut,
    BillingResponse,
    CheckoutRequest,
    PlanOut,
    PriceOut,
    RedirectResponse,
    UsageOut,
)
from app.schemas.common import ErrorResponse
from app.services.billing.plans import PLANS, Allowance, allowance, entitled_plan
from app.services.billing.stripe_billing import StripeBillingDep, get_stripe_client, price_for
from app.services.billing.webhooks import SubscriptionSync
from app.services.workspace_limits import month_start

logger = logging.getLogger(__name__)

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
    auth: AuthDep,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    client: Annotated[stripe.StripeClient | None, Depends(get_stripe_client)],
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
        pro_prices=await _pro_prices(redis, client),
    )


async def _pro_prices(redis: Redis, client: stripe.StripeClient | None) -> list[PriceOut]:
    """Pro's prices as Stripe has them, kept an hour in Redis. Prices are shown, never
    charged, from here: Checkout charges what Stripe has, whatever this says."""
    if client is None or not settings.BILLING_ENABLED:
        return []
    key = "billing:pro_prices"
    try:
        if cached := await redis.get(key):
            return [PriceOut.model_validate(p) for p in json.loads(cached)]
    except Exception:
        logger.warning("Price cache read failed", exc_info=True)
    prices = []
    for interval in ("month", "year"):
        price_id = price_for(interval)
        if not price_id:
            continue
        try:
            price = await client.v1.prices.retrieve_async(price_id)
        except stripe.StripeError:
            logger.warning("Could not read Stripe price %s", price_id, exc_info=True)
            return []
        prices.append(
            PriceOut(interval=interval, unit_amount=price.unit_amount or 0, currency=price.currency)
        )
    try:
        await redis.set(key, json.dumps([p.model_dump() for p in prices]), ex=60 * 60)
    except Exception:
        logger.warning("Price cache write failed", exc_info=True)
    return prices


# Paying is the owner's or an admin's call.
MANAGE = [*PROTECTED, require_role(Role.ADMIN)]
_STRIPE_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Billing is off, or nothing to manage yet"},
    503: {"model": ErrorResponse, "description": "Stripe is not configured or unavailable"},
}


@router.post(
    "/checkout",
    dependencies=MANAGE,
    summary="Start a Stripe Checkout to subscribe to Pro (Admin)",
    responses={
        **_STRIPE_RESPONSES,
        409: {"model": ErrorResponse, "description": "Already on Pro"},
    },
)
async def checkout(
    payload: CheckoutRequest, auth: AuthDep, billing: StripeBillingDep
) -> RedirectResponse:
    email = auth.user.email if auth.user is not None else auth.tenant.email
    return RedirectResponse(url=await billing.checkout_url(auth.tenant, payload.interval, email))


@router.post(
    "/portal",
    dependencies=MANAGE,
    summary="Open the Stripe Customer Portal: change plan, cancel, cards, invoices (Admin)",
    responses=_STRIPE_RESPONSES,
)
async def portal(auth: AuthDep, billing: StripeBillingDep) -> RedirectResponse:
    return RedirectResponse(url=await billing.portal_url(auth.tenant))


@router.post(
    "/sync",
    dependencies=MANAGE,
    summary="Bring the plan up to date from Stripe now, e.g. right after checkout (Admin)",
    responses=_STRIPE_RESPONSES,
)
async def sync(
    auth: AuthDep,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    client: Annotated[stripe.StripeClient | None, Depends(get_stripe_client)],
) -> BillingResponse:
    if not settings.BILLING_ENABLED:
        raise BadRequestError("Billing is not enabled on this server.")
    if client is None:
        raise ServiceUnavailableError("Payments are not configured on this server.")
    try:
        await SubscriptionSync(session, client).sync_customer(auth.tenant)
    except stripe.StripeError as exc:
        raise ServiceUnavailableError(f"Stripe is unavailable: {exc}") from exc
    await session.commit()
    return await get_billing(auth, session, redis, client)


@router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[stripe.StripeClient | None, Depends(get_stripe_client)],
) -> dict[str, str]:
    """Called by Stripe, not by users: authenticated by its signature, not an API key."""
    secret = settings.RECO_STRIPE_WEBHOOK_SECRET
    if secret is None or client is None:
        raise ServiceUnavailableError("Stripe webhooks are not configured on this server.")
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload, request.headers.get("stripe-signature"), secret.get_secret_value()
        )
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise BadRequestError("Invalid Stripe webhook signature or payload.") from exc
    try:
        outcome = await SubscriptionSync(session, client).handle(event)
        await session.commit()
    except Exception:
        # Nothing is saved, the event included: answering 500 makes Stripe send it again.
        await session.rollback()
        logger.exception("Stripe event %s failed", event["id"])
        raise
    return {"outcome": outcome}
