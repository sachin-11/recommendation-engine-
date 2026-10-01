import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import stripe
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models import StripeEvent, Tenant
from app.models.tenant import Plan
from app.services.billing.stripe_billing import get_stripe_client
from tests.conftest import TenantAuth, register_tenant

BILLING = "/api/v1/billing"
SECRET = "whsec_test_secret"
PERIOD_END = 1_800_000_000


def sign(payload: str, secret: str = SECRET) -> str:
    """A Stripe-Signature header, made the way Stripe makes it."""
    timestamp = int(time.time())
    digest = hmac.new(secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def subscription(
    tenant_id: str | None,
    status: str = "active",
    price: str = "price_month",
    sub_id: str = "sub_1",
    created: int = 1,
    cancel_at_period_end: bool = False,
) -> dict[str, Any]:
    return {
        "id": sub_id,
        "object": "subscription",
        "customer": "cus_1",
        "status": status,
        "created": created,
        "cancel_at_period_end": cancel_at_period_end,
        "metadata": {"tenant_id": tenant_id} if tenant_id else {},
        "items": {"data": [{"price": {"id": price}, "current_period_end": PERIOD_END}]},
    }


class FakeSubscriptions:
    def __init__(self) -> None:
        self.current: dict[str, dict[str, Any]] = {}
        self.retrieved: list[str] = []
        self.fail = False

    async def retrieve_async(self, subscription_id: str) -> dict[str, Any]:
        self.retrieved.append(subscription_id)
        if self.fail:
            raise stripe.APIConnectionError("Stripe is down")
        return self.current[subscription_id]

    async def list_async(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"data": [s for s in self.current.values() if s["customer"] == params["customer"]]}


class FakeStripe:
    def __init__(self) -> None:
        self.subscriptions = FakeSubscriptions()

        class _V1:
            pass

        self.v1 = _V1()
        self.v1.subscriptions = self.subscriptions  # type: ignore[attr-defined]


@pytest.fixture
def fake_stripe(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "RECO_STRIPE_PRO_MONTHLY_PRICE_ID", "price_month")
    monkeypatch.setattr(settings, "RECO_STRIPE_PRO_YEARLY_PRICE_ID", "price_year")
    monkeypatch.setattr(settings, "RECO_STRIPE_WEBHOOK_SECRET", SecretStr(SECRET))
    fake = FakeStripe()
    app.dependency_overrides[get_stripe_client] = lambda: fake
    return fake


@pytest.fixture
async def owner(client: AsyncClient) -> TenantAuth:
    return await register_tenant(client, "owner@acme.example")


async def deliver(
    client: AsyncClient, event_type: str, obj: dict[str, Any], event_id: str | None = None
) -> Any:
    payload = json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex}",
            "object": "event",
            "type": event_type,
            "data": {"object": obj},
        }
    )
    return await client.post(
        f"{BILLING}/webhook",
        content=payload,
        headers={"Stripe-Signature": sign(payload), "Content-Type": "application/json"},
    )


async def _tenant(session_factory: async_sessionmaker[AsyncSession], auth: TenantAuth) -> Tenant:
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(auth.tenant_id))
    assert tenant is not None
    return tenant


async def test_checkout_completed_makes_the_workspace_pro(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_stripe.subscriptions.current["sub_1"] = subscription(owner.tenant_id)

    response = await deliver(
        client, "checkout.session.completed", {"mode": "subscription", "subscription": "sub_1"}
    )

    assert response.status_code == 200 and response.json() == {"outcome": "synced"}
    tenant = await _tenant(session_factory, owner)
    assert (tenant.plan, tenant.subscription_status) == (Plan.PRO, "active")
    assert tenant.stripe_subscription_id == "sub_1" and tenant.stripe_customer_id == "cus_1"
    assert tenant.current_period_end is not None
    # SQLite returns it without a time zone; it is UTC either way.
    assert tenant.current_period_end.replace(tzinfo=UTC) == datetime.fromtimestamp(PERIOD_END, UTC)
    billing = (await client.get(BILLING, headers=owner.headers)).json()
    assert billing["plan"] == "PRO" and billing["allowance"]["llm_features"] is True


async def test_a_repeated_event_is_handled_once(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_stripe.subscriptions.current["sub_1"] = subscription(owner.tenant_id)
    obj = subscription(owner.tenant_id)

    first = await deliver(client, "customer.subscription.created", obj, event_id="evt_1")
    second = await deliver(client, "customer.subscription.created", obj, event_id="evt_1")

    assert (first.json()["outcome"], second.json()["outcome"]) == ("synced", "duplicate")
    assert fake_stripe.subscriptions.retrieved == ["sub_1"]
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(StripeEvent)) == 1


async def test_events_out_of_order_still_end_in_the_current_state(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Stripe's current state: cancelled. The "deleted" event arrives before an older
    # "updated" that still says active.
    fake_stripe.subscriptions.current["sub_1"] = subscription(owner.tenant_id, status="canceled")
    await deliver(
        client, "customer.subscription.deleted", subscription(owner.tenant_id, status="canceled")
    )
    await deliver(
        client, "customer.subscription.updated", subscription(owner.tenant_id, status="active")
    )

    tenant = await _tenant(session_factory, owner)
    assert tenant.subscription_status == "canceled"
    billing = (await client.get(BILLING, headers=owner.headers)).json()
    assert billing["plan"] == "FREE" and billing["subscribed_plan"] == "PRO"


async def test_a_failed_payment_keeps_pro_during_the_grace_period(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
) -> None:
    fake_stripe.subscriptions.current["sub_1"] = subscription(owner.tenant_id, status="past_due")
    invoice = {"object": "invoice", "parent": {"subscription_details": {"subscription": "sub_1"}}}

    response = await deliver(client, "invoice.payment_failed", invoice)

    assert response.json()["outcome"] == "synced"
    billing = (await client.get(BILLING, headers=owner.headers)).json()
    assert (billing["plan"], billing["subscription_status"]) == ("PRO", "past_due")


async def test_other_apps_events_are_left_alone(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_stripe.subscriptions.current["sub_x"] = subscription(
        None, price="price_other_app", sub_id="sub_x"
    )
    fake_stripe.subscriptions.current["sub_y"] = subscription(
        None, sub_id="sub_y"
    )  # ours, no workspace

    other_price = await deliver(client, "customer.subscription.created", {"id": "sub_x"})
    no_workspace = await deliver(client, "customer.subscription.created", {"id": "sub_y"})
    unrelated = await deliver(client, "product.created", {"id": "prod_1"})

    assert [r.json()["outcome"] for r in (other_price, no_workspace, unrelated)] == [
        "not_ours",
        "not_ours",
        "ignored",
    ]
    assert (await _tenant(session_factory, owner)).plan is Plan.FREE


async def test_a_failure_is_retried_by_stripe(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_stripe.subscriptions.current["sub_1"] = subscription(owner.tenant_id)
    fake_stripe.subscriptions.fail = True
    with pytest.raises(stripe.APIConnectionError):  # the test client re-raises; Stripe sees a 500
        await deliver(client, "customer.subscription.created", {"id": "sub_1"}, event_id="evt_9")

    fake_stripe.subscriptions.fail = False
    retried = await deliver(
        client, "customer.subscription.created", {"id": "sub_1"}, event_id="evt_9"
    )

    assert (
        retried.json()["outcome"] == "synced"
    )  # not "duplicate": nothing was saved the first time
    assert (await _tenant(session_factory, owner)).plan is Plan.PRO


async def test_signatures_are_checked(
    client: AsyncClient, fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps({"id": "evt_1", "type": "x", "data": {"object": {}}})
    forged = await client.post(
        f"{BILLING}/webhook",
        content=payload,
        headers={"Stripe-Signature": sign(payload, "whsec_wrong")},
    )
    unsigned = await client.post(f"{BILLING}/webhook", content=payload)
    monkeypatch.setattr(settings, "RECO_STRIPE_WEBHOOK_SECRET", None)
    unconfigured = await client.post(
        f"{BILLING}/webhook", content=payload, headers={"Stripe-Signature": sign(payload)}
    )

    assert (forged.status_code, unsigned.status_code, unconfigured.status_code) == (400, 400, 503)


async def test_sync_after_checkout_picks_the_live_subscription(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(owner.tenant_id))
        assert tenant is not None
        tenant.stripe_customer_id = "cus_1"
        await session.commit()
    subs = fake_stripe.subscriptions.current
    subs["old"] = subscription(owner.tenant_id, status="canceled", sub_id="old", created=1)
    subs["new"] = subscription(
        owner.tenant_id, status="active", sub_id="new", created=2, price="price_year"
    )

    response = await client.post(f"{BILLING}/sync", headers=owner.owner_headers)

    assert response.status_code == 200, response.text
    assert (response.json()["plan"], response.json()["subscription_status"]) == ("PRO", "active")
    assert (await _tenant(session_factory, owner)).stripe_subscription_id == "new"
