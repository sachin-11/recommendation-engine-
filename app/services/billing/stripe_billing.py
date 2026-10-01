"""Upgrading through Stripe: hosted Checkout to subscribe, the Customer Portal to manage.

The app never sees a card. Checkout and the Portal are pages Stripe hosts; this service
creates sessions for them and returns their URL. Whether the subscription took effect is
learned from Stripe's webhooks (webhooks.py), not from the browser coming back, since a
user can close the tab before that happens.

Each workspace is one Stripe customer, created on its first checkout and remembered on the
workspace; the workspace id is also put on the customer and subscription metadata, so a
webhook can always be traced back to its workspace.
"""

import logging
from typing import Annotated, Literal

import stripe
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, ConflictError, ServiceUnavailableError
from app.models.tenant import Plan, Tenant
from app.services.billing.plans import PAID_STATUSES

logger = logging.getLogger(__name__)

Interval = Literal["month", "year"]


def stripe_client() -> stripe.StripeClient | None:
    """None when no Stripe key is configured."""
    if settings.STRIPE_SECRET_KEY is None:
        return None
    return stripe.StripeClient(
        settings.STRIPE_SECRET_KEY.get_secret_value(),
        http_client=stripe.HTTPXClient(),
        max_network_retries=2,
    )


def price_for(interval: Interval) -> str | None:
    if interval == "year":
        return settings.RECO_STRIPE_PRO_YEARLY_PRICE_ID
    return settings.RECO_STRIPE_PRO_MONTHLY_PRICE_ID


class StripeBilling:
    def __init__(self, session: AsyncSession, client: stripe.StripeClient | None) -> None:
        self._session = session
        self._client = client

    def _require(self) -> stripe.StripeClient:
        if not settings.BILLING_ENABLED:
            raise BadRequestError("Billing is not enabled on this server.")
        if self._client is None:
            raise ServiceUnavailableError("Payments are not configured on this server.")
        return self._client

    async def checkout_url(self, tenant: Tenant, interval: Interval, email: str) -> str:
        """A Stripe Checkout page that subscribes the workspace to Pro."""
        client = self._require()
        if tenant.plan is Plan.PRO and tenant.subscription_status in PAID_STATUSES:
            raise ConflictError("This workspace is already on Pro. Manage it under Billing.")
        price = price_for(interval)
        if price is None:
            raise ServiceUnavailableError(f"No Pro price is configured for a {interval}ly plan.")
        customer = await self._customer(client, tenant, email)
        billing_page = f"{settings.DASHBOARD_URL}/dashboard/billing"
        try:
            checkout = await client.v1.checkout.sessions.create_async(
                params={
                    "mode": "subscription",
                    "customer": customer,
                    "line_items": [{"price": price, "quantity": 1}],
                    "client_reference_id": str(tenant.id),
                    "subscription_data": {"metadata": {"tenant_id": str(tenant.id)}},
                    "allow_promotion_codes": True,
                    "success_url": f"{billing_page}?checkout=success",
                    "cancel_url": f"{billing_page}?checkout=cancelled",
                }
            )
        except stripe.StripeError as exc:
            raise _unavailable(exc) from exc
        if not checkout.url:
            raise ServiceUnavailableError("Stripe did not return a checkout page.")
        return checkout.url

    async def portal_url(self, tenant: Tenant) -> str:
        """The Stripe Customer Portal: change or cancel the plan, cards, invoices."""
        client = self._require()
        if not tenant.stripe_customer_id:
            raise BadRequestError("This workspace has no billing account yet; upgrade first.")
        try:
            portal = await client.v1.billing_portal.sessions.create_async(
                params={
                    "customer": tenant.stripe_customer_id,
                    "return_url": f"{settings.DASHBOARD_URL}/dashboard/billing",
                }
            )
        except stripe.StripeError as exc:
            raise _unavailable(exc) from exc
        return portal.url

    async def _customer(self, client: stripe.StripeClient, tenant: Tenant, email: str) -> str:
        if tenant.stripe_customer_id:
            return tenant.stripe_customer_id
        try:
            customer = await client.v1.customers.create_async(
                params={
                    "email": email,
                    "name": tenant.name,
                    "metadata": {"tenant_id": str(tenant.id)},
                },
                # A double click creates one customer, not two.
                options={"idempotency_key": f"customer-{tenant.id}"},
            )
        except stripe.StripeError as exc:
            raise _unavailable(exc) from exc
        tenant.stripe_customer_id = customer.id
        await self._session.commit()
        return customer.id


def _unavailable(exc: stripe.StripeError) -> ServiceUnavailableError:
    logger.warning("Stripe request failed: %s", exc)
    return ServiceUnavailableError(f"Payments are unavailable: {exc.user_message or exc}")


def get_stripe_client() -> stripe.StripeClient | None:
    return stripe_client()


def get_stripe_billing(
    session: Annotated[AsyncSession, Depends(get_db)],
    client: Annotated[stripe.StripeClient | None, Depends(get_stripe_client)],
) -> StripeBilling:
    return StripeBilling(session, client)


StripeBillingDep = Annotated[StripeBilling, Depends(get_stripe_billing)]
