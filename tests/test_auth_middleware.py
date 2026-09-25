from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ApiKey, Tenant
from tests.conftest import TenantAuth

PROTECTED = ["/api/v1/items", "/api/v1/index/stats"]


@pytest.mark.parametrize("path", PROTECTED)
async def test_valid_key_passes(client: AsyncClient, hr_tenant: TenantAuth, path: str) -> None:
    response = await client.get(path, headers=hr_tenant.headers)
    assert response.status_code == 200, response.text


async def test_valid_key_records_last_used(
    client: AsyncClient,
    hr_tenant: TenantAuth,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.get("/api/v1/items", headers=hr_tenant.headers)

    async with session_factory() as session:
        key = (await session.scalars(select(ApiKey).where(ApiKey.is_session.is_(False)))).one()
        assert key.last_used_at is not None


@pytest.mark.parametrize("path", PROTECTED)
async def test_missing_key_returns_401(client: AsyncClient, path: str) -> None:
    response = await client.get(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["WWW-Authenticate"] == "X-API-Key"


async def test_invalid_key_returns_401(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    response = await client.get("/api/v1/items", headers={"X-API-Key": hr_tenant.api_key + "x"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid API key"


async def test_expired_key_returns_401(
    client: AsyncClient,
    hr_tenant: TenantAuth,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(ApiKey).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    response = await client.get("/api/v1/items", headers=hr_tenant.headers)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "API key has expired"


async def test_key_with_future_expiry_passes(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    created = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "expiring", "expires_at": expires},
        headers=hr_tenant.headers,
    )
    assert created.status_code == 201
    assert created.json()["expires_at"] is not None

    response = await client.get("/api/v1/items", headers={"X-API-Key": created.json()["api_key"]})
    assert response.status_code == 200


async def test_creating_already_expired_key_is_rejected(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    response = await client.post(
        "/api/v1/me/api-keys",
        json={"name": "old", "expires_at": past},
        headers=hr_tenant.headers,
    )
    assert response.status_code == 422


async def test_revoked_key_returns_401(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    keys = (await client.get("/api/v1/me/api-keys", headers=hr_tenant.headers)).json()
    await client.delete(f"/api/v1/me/api-keys/{keys[0]['id']}", headers=hr_tenant.headers)

    response = await client.get("/api/v1/items", headers=hr_tenant.headers)

    assert response.status_code == 401


async def test_inactive_tenant_returns_403(
    client: AsyncClient,
    hr_tenant: TenantAuth,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await session.execute(update(Tenant).values(is_active=False))
        await session.commit()

    response = await client.get("/api/v1/items", headers=hr_tenant.headers)

    assert response.status_code == 403


async def test_registration_does_not_need_a_key(client: AsyncClient) -> None:
    # Sign-up stays open; everything that acts on a tenant needs a key.
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "New",
            "email": "new@acme.example",
            "password": "new-password-123",
            "domain_type": "EDTECH",
        },
    )
    assert response.status_code == 201


async def test_tenant_api_key_cannot_use_admin_routes(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    response = await client.post(
        f"/api/v1/tenants/{hr_tenant.tenant_id}/api-keys",
        json={"name": "escalate"},
        headers=hr_tenant.headers,
    )
    assert response.status_code == 401
