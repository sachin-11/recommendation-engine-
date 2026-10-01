"""Keeping each workspace's plan in step with its Stripe subscription.

Stripe tells us what happened by webhook. Three things make that tricky, and each has
a rule here:

- Delivery is at least once: an event can arrive twice. Each handled event's id is
  stored (StripeEvent), in the same transaction as its effect, so a repeat is skipped
  and a failed one is retried by Stripe from scratch.
- Events can arrive out of order ("updated" after "deleted"). So an event's payload is
  only a hint of which subscription changed: its current state is fetched from Stripe
  and copied, which is right whatever order the events came in.
- One Stripe account may serve other apps. A subscription with no workspace behind it,
  or without one of this app's Pro prices, is left alone.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.stripe_event import StripeEvent
from app.models.tenant import Plan, Tenant

logger = logging.getLogger(__name__)

SUBSCRIPTION_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
    }
)
INVOICE_EVENTS = frozenset({"invoice.paid", "invoice.payment_failed"})
# Statuses of a subscription that may still turn into a paid one, best first.
_PREFERENCE = ("active", "trialing", "past_due", "unpaid", "incomplete", "paused")


def pro_prices() -> set[str]:
    return {
        p
        for p in (
            settings.RECO_STRIPE_PRO_MONTHLY_PRICE_ID,
            settings.RECO_STRIPE_PRO_YEARLY_PRICE_ID,
        )
        if p
    }


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field of a Stripe object or a plain dict."""
    try:
        value = obj[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


def _items(subscription: Any) -> list[Any]:
    return list(_get(_get(subscription, "items", {}), "data", []))


def is_ours(subscription: Any) -> bool:
    return any(_get(_get(item, "price", {}), "id") in pro_prices() for item in _items(subscription))


def apply_subscription(tenant: Tenant, subscription: Any) -> None:
    """Copy the subscription's current state onto the workspace."""
    tenant.plan = Plan.PRO
    tenant.stripe_subscription_id = _get(subscription, "id")
    tenant.subscription_status = _get(subscription, "status")
    tenant.cancel_at_period_end = bool(_get(subscription, "cancel_at_period_end", False))
    # Newer API versions keep the period on each item; older ones on the subscription.
    ends = [_get(item, "current_period_end") for item in _items(subscription)]
    end = max((e for e in ends if e), default=_get(subscription, "current_period_end"))
    tenant.current_period_end = datetime.fromtimestamp(end, UTC) if end else None
    customer = _get(subscription, "customer")
    if isinstance(customer, str) and not tenant.stripe_customer_id:
        tenant.stripe_customer_id = customer


class SubscriptionSync:
    def __init__(self, session: AsyncSession, client: stripe.StripeClient) -> None:
        self._session = session
        self._client = client

    async def handle(self, event: Any) -> str:
        """Apply one verified webhook event. Returns what was done; the caller commits.
        Raises to make Stripe retry (nothing is saved then)."""
        event_id, kind = _get(event, "id"), _get(event, "type")
        if await self._session.get(StripeEvent, event_id) is not None:
            return "duplicate"
        subscription_id = self._subscription_of(kind, _get(_get(event, "data", {}), "object", {}))
        outcome = "ignored"
        if subscription_id:
            outcome = "synced" if await self.sync_subscription(subscription_id) else "not_ours"
        self._session.add(StripeEvent(id=event_id, type=kind, outcome=outcome))
        logger.info("Stripe event %s (%s): %s", event_id, kind, outcome)
        return outcome

    @staticmethod
    def _subscription_of(kind: str, obj: Any) -> str | None:
        found: Any = None
        if kind == "checkout.session.completed" and _get(obj, "mode") == "subscription":
            found = _get(obj, "subscription")
        elif kind in SUBSCRIPTION_EVENTS:
            found = _get(obj, "id")
        elif kind in INVOICE_EVENTS:
            # Newer API versions: invoice.parent.subscription_details.subscription.
            details = _get(_get(obj, "parent", {}), "subscription_details", {})
            found = _get(details, "subscription") or _get(obj, "subscription")
        return found if isinstance(found, str) else None

    async def sync_subscription(self, subscription_id: str) -> Tenant | None:
        """Fetch the subscription from Stripe and copy it onto its workspace, if it has one
        and is for this app's Pro plan."""
        subscription = await self._client.v1.subscriptions.retrieve_async(subscription_id)
        if not is_ours(subscription):
            return None
        tenant = await self._tenant_for(subscription)
        if tenant is None:
            logger.warning("Stripe subscription %s has no workspace", subscription_id)
            return None
        apply_subscription(tenant, subscription)
        return tenant

    async def sync_customer(self, tenant: Tenant) -> None:
        """Bring the workspace up to date from its Stripe customer's subscriptions, e.g. right
        after checkout, before the webhook arrives."""
        if not tenant.stripe_customer_id:
            return
        listed = await self._client.v1.subscriptions.list_async(
            params={"customer": tenant.stripe_customer_id, "status": "all", "limit": 20}
        )
        ours = [s for s in _get(listed, "data", []) if is_ours(s)]
        if not ours:
            return

        def rank(s: Any) -> tuple[int, int]:
            status = _get(s, "status")
            order = _PREFERENCE.index(status) if status in _PREFERENCE else len(_PREFERENCE)
            return order, -int(_get(s, "created", 0))

        apply_subscription(tenant, min(ours, key=rank))

    async def _tenant_for(self, subscription: Any) -> Tenant | None:
        tenant_id = _get(_get(subscription, "metadata", {}), "tenant_id")
        if tenant_id:
            try:
                tenant = await self._session.get(Tenant, uuid.UUID(tenant_id))
            except ValueError:
                tenant = None
            if tenant is not None:
                return tenant
        customer = _get(subscription, "customer")
        if not isinstance(customer, str):
            return None
        by_customer: Tenant | None = await self._session.scalar(
            select(Tenant).where(Tenant.stripe_customer_id == customer)
        )
        return by_customer
