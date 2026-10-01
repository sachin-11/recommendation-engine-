import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import stripe
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models import Tenant
from app.models.tenant import Plan
from app.services.billing.stripe_billing import get_stripe_client
from tests.conftest import TenantAuth, register_tenant

BILLING = "/api/v1/billing"


@dataclass
class _Obj:
    id: str = ""
    url: str | None = None


@dataclass
class FakeStripe:
    """Records what would be sent to Stripe; `fail` makes every call raise."""

    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    def __post_init__(self) -> None:
        fake = self

        class _Resource:
            def __init__(self, name: str, result: _Obj) -> None:
                self._name, self._result = name, result

            async def create_async(
                self, params: dict[str, Any], options: dict[str, Any] | None = None
            ) -> _Obj:
                fake.calls.append((self._name, params, options or {}))
                if fake.fail:
                    raise stripe.APIConnectionError("Stripe is down")
                return self._result

        class _Namespace:
            pass

        self.v1 = _Namespace()
        self.v1.customers = _Resource("customers", _Obj(id="cus_123"))  # type: ignore[attr-defined]
        self.v1.checkout = _Namespace()  # type: ignore[attr-defined]
        self.v1.checkout.sessions = _Resource(  # type: ignore[attr-defined]
            "checkout", _Obj(id="cs_1", url="https://checkout.stripe.test/cs_1")
        )
        self.v1.billing_portal = _Namespace()  # type: ignore[attr-defined]
        self.v1.billing_portal.sessions = _Resource(  # type: ignore[attr-defined]
            "portal", _Obj(id="bps_1", url="https://billing.stripe.test/p_1")
        )

    def made(self, name: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        return [(params, options) for n, params, options in self.calls if n == name]


@pytest.fixture
def fake_stripe(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "RECO_STRIPE_PRO_MONTHLY_PRICE_ID", "price_month")
    monkeypatch.setattr(settings, "RECO_STRIPE_PRO_YEARLY_PRICE_ID", "price_year")
    fake = FakeStripe()
    app.dependency_overrides[get_stripe_client] = lambda: fake
    return fake


@pytest.fixture
async def owner(client: AsyncClient) -> TenantAuth:
    return await register_tenant(client, "owner@acme.example")


async def test_checkout_subscribes_through_stripe(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    monthly = await client.post(f"{BILLING}/checkout", json={}, headers=owner.owner_headers)
    yearly = await client.post(
        f"{BILLING}/checkout", json={"interval": "year"}, headers=owner.owner_headers
    )

    assert monthly.status_code == 200, monthly.text
    assert monthly.json() == {"url": "https://checkout.stripe.test/cs_1"}
    assert yearly.status_code == 200
    # One Stripe customer per workspace, created once and remembered.
    ((customer, options),) = fake_stripe.made("customers")
    assert customer["email"] == "owner@acme.example"
    assert customer["metadata"] == {"tenant_id": owner.tenant_id}
    assert options == {"idempotency_key": f"customer-{owner.tenant_id}"}
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(owner.tenant_id))
    assert tenant is not None and tenant.stripe_customer_id == "cus_123"

    (first, _), (second, _) = fake_stripe.made("checkout")
    assert first["mode"] == "subscription" and first["customer"] == "cus_123"
    assert [li["price"] for li in (first["line_items"][0], second["line_items"][0])] == [
        "price_month",
        "price_year",
    ]
    assert first["client_reference_id"] == owner.tenant_id
    assert first["subscription_data"] == {"metadata": {"tenant_id": owner.tenant_id}}
    assert first["success_url"].endswith("/dashboard/billing?checkout=success")
    assert first["cancel_url"].endswith("/dashboard/billing?checkout=cancelled")


async def test_portal(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    before = await client.post(f"{BILLING}/portal", headers=owner.owner_headers)
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(owner.tenant_id))
        assert tenant is not None
        tenant.stripe_customer_id = "cus_123"
        await session.commit()
    after = await client.post(f"{BILLING}/portal", headers=owner.owner_headers)

    assert before.status_code == 400
    assert after.json() == {"url": "https://billing.stripe.test/p_1"}
    ((params, _),) = fake_stripe.made("portal")
    assert params["customer"] == "cus_123"
    assert params["return_url"].endswith("/dashboard/billing")


async def test_already_on_pro_is_409(
    client: AsyncClient,
    owner: TenantAuth,
    fake_stripe: FakeStripe,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(owner.tenant_id))
        assert tenant is not None
        tenant.plan, tenant.subscription_status = Plan.PRO, "active"
        await session.commit()

    response = await client.post(f"{BILLING}/checkout", json={}, headers=owner.owner_headers)

    assert response.status_code == 409
    assert fake_stripe.calls == []


async def test_who_may_pay_and_when(
    client: AsyncClient, owner: TenantAuth, fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An integration key acts as Developer: paying is for owners and admins.
    developer = await client.post(f"{BILLING}/checkout", json={}, headers=owner.headers)
    bad_interval = await client.post(
        f"{BILLING}/checkout", json={"interval": "week"}, headers=owner.owner_headers
    )
    fake_stripe.fail = True
    stripe_down = await client.post(f"{BILLING}/checkout", json={}, headers=owner.owner_headers)
    monkeypatch.setattr(settings, "BILLING_ENABLED", False)
    billing_off = await client.post(f"{BILLING}/checkout", json={}, headers=owner.owner_headers)

    assert developer.status_code == 403
    assert bad_interval.status_code == 422
    assert stripe_down.status_code == 503
    assert billing_off.status_code == 400


async def test_without_a_stripe_key_is_503(
    app: FastAPI, client: AsyncClient, owner: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    app.dependency_overrides[get_stripe_client] = lambda: None

    response = await client.post(f"{BILLING}/checkout", json={}, headers=owner.owner_headers)

    assert response.status_code == 503
