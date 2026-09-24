import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import API_KEY_PREFIX, hash_api_key
from app.models import ApiKey
from app.schemas.tenant import API_KEY_WARNING
from tests.conftest import ADMIN_HEADERS

TENANTS = "/api/v1/tenants"


@pytest.fixture(autouse=True)
def as_admin(client: AsyncClient) -> None:
    # /tenants is operator-only; every request in this module carries the admin key.
    client.headers.update(ADMIN_HEADERS)


HR_CONFIG: dict[str, Any] = {
    "primary_embedding_field": "description",
    "searchable_fields": ["title", "description", "tags"],
    "filter_fields": ["location", "category"],
    "item_label": "job",
}


def tenant_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Acme Hiring",
        "email": "ops@acme.example",
        "domain_type": "HR",
        "domain_config": HR_CONFIG,
    }
    payload.update(overrides)
    return payload


async def create_tenant(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post(TENANTS, json=tenant_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# --- Tenants ---


async def test_create_tenant_happy_path(client: AsyncClient) -> None:
    response = await client.post(TENANTS, json=tenant_payload())

    assert response.status_code == 201
    body = response.json()
    uuid.UUID(body["id"])
    assert body["name"] == "Acme Hiring"
    assert body["email"] == "ops@acme.example"
    assert body["domain_type"] == "HR"
    assert body["domain_config"] == HR_CONFIG
    assert body["is_active"] is True
    assert body["created_at"] and body["updated_at"]

    fetched = await client.get(f"{TENANTS}/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


async def test_create_tenant_uses_domain_preset_when_config_omitted(client: AsyncClient) -> None:
    payload = tenant_payload(domain_type="ECOMMERCE")
    del payload["domain_config"]

    response = await client.post(TENANTS, json=payload)

    assert response.status_code == 201
    assert response.json()["domain_config"]["item_label"] == "product"


async def test_custom_domain_requires_config(client: AsyncClient) -> None:
    payload = tenant_payload(domain_type="CUSTOM")
    del payload["domain_config"]

    response = await client.post(TENANTS, json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_duplicate_email_returns_409(client: AsyncClient) -> None:
    await create_tenant(client)

    # Emails are normalised, so a case variant is still a duplicate.
    response = await client.post(TENANTS, json=tenant_payload(email="OPS@Acme.Example"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_get_unknown_tenant_returns_clean_404(client: AsyncClient) -> None:
    response = await client.get(f"{TENANTS}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_validation_errors_use_clean_format(client: AsyncClient) -> None:
    response = await client.post(TENANTS, json=tenant_payload(email="not-an-email"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(detail["field"] == "body.email" for detail in error["details"])


# --- API keys ---


async def test_api_key_generation_returns_plain_key_once(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await create_tenant(client)
    keys_url = f"{TENANTS}/{tenant['id']}/api-keys"

    created = await client.post(keys_url, json={"name": "production-backend"})

    assert created.status_code == 201
    body = created.json()
    plain_key = body["api_key"]
    assert plain_key.startswith(API_KEY_PREFIX)
    assert len(plain_key) > len(API_KEY_PREFIX) + 32
    assert body["warning"] == API_KEY_WARNING
    assert body["key_prefix"] == plain_key[len(API_KEY_PREFIX) :][:8]
    assert body["display_key"] == f"{API_KEY_PREFIX}{body['key_prefix']}..."
    assert "key_hash" not in body

    # Listing never exposes the plain key or its hash.
    listed = await client.get(keys_url)
    assert listed.status_code == 200
    assert plain_key not in listed.text
    [item] = listed.json()
    assert item["id"] == body["id"]
    assert "api_key" not in item and "key_hash" not in item

    # Only the hash is persisted.
    async with session_factory() as session:
        stored = (await session.execute(select(ApiKey))).scalar_one()
    assert stored.key_hash == hash_api_key(plain_key)
    assert stored.key_hash != plain_key


async def test_each_api_key_is_unique(client: AsyncClient) -> None:
    tenant = await create_tenant(client)
    keys_url = f"{TENANTS}/{tenant['id']}/api-keys"

    first = (await client.post(keys_url, json={"name": "a"})).json()["api_key"]
    second = (await client.post(keys_url, json={"name": "b"})).json()["api_key"]

    assert first != second


async def test_revoke_api_key(client: AsyncClient) -> None:
    tenant = await create_tenant(client)
    keys_url = f"{TENANTS}/{tenant['id']}/api-keys"
    key_id = (await client.post(keys_url, json={"name": "temp"})).json()["id"]

    revoked = await client.delete(f"{keys_url}/{key_id}")

    assert revoked.status_code == 204
    [item] = (await client.get(keys_url)).json()
    assert item["is_active"] is False
    # Revoking a key that belongs to no one (or another tenant) is a 404.
    assert (await client.delete(f"{keys_url}/{uuid.uuid4()}")).status_code == 404


async def test_api_key_for_unknown_tenant_returns_404(client: AsyncClient) -> None:
    response = await client.post(f"{TENANTS}/{uuid.uuid4()}/api-keys", json={"name": "x"})

    assert response.status_code == 404


# --- Health ---


async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["redis"] == "ok"


# --- Admin authentication ---


async def test_tenant_routes_require_the_admin_key(client: AsyncClient) -> None:
    created = await client.post(TENANTS, json=tenant_payload())
    tenant_id = created.json()["id"]
    del client.headers["X-Admin-Key"]

    missing = await client.get(f"{TENANTS}/{tenant_id}")
    wrong = await client.get(f"{TENANTS}/{tenant_id}", headers={"X-Admin-Key": "x" * 40})
    mint_key = await client.post(f"{TENANTS}/{tenant_id}/api-keys", json={"name": "stolen"})

    assert (missing.status_code, wrong.status_code, mint_key.status_code) == (401, 401, 401)
    assert missing.json()["error"]["message"] == "Missing X-Admin-Key header"


async def test_a_tenant_api_key_is_not_an_admin_key(client: AsyncClient) -> None:
    tenant = await create_tenant(client)
    key = (await client.post(f"{TENANTS}/{tenant['id']}/api-keys", json={"name": "k"})).json()
    del client.headers["X-Admin-Key"]

    response = await client.get(f"{TENANTS}/{tenant['id']}", headers={"X-API-Key": key["api_key"]})

    assert response.status_code == 401


async def test_admin_api_is_disabled_without_admin_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_API_KEY", None)

    response = await client.post(TENANTS, json=tenant_payload())

    assert response.status_code == 403
    assert "ADMIN_API_KEY" in response.json()["error"]["message"]
