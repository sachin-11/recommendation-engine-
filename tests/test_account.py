"""Dashboard accounts: register, login/logout, /me, API keys, domain config, deletion."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.passwords import hash_password, verify_password
from app.models import ApiKey, Tenant
from tests.conftest import ADMIN_HEADERS, link_token
from tests.fakes import FakeVectorStore

AUTH = "/api/v1/auth"
ME = "/api/v1/me"
PASSWORD = "correct horse battery"


def registration(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Acme Hiring",
        "email": "Owner@Acme.example",
        "password": PASSWORD,
        "domain_type": "HR",
        **overrides,
    }


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post(f"{AUTH}/register", json=registration(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


async def verify(client: AsyncClient, email: str = "owner@acme.example") -> None:
    token = link_token(email, "/verify-email")
    response = await client.post(f"{AUTH}/verify-email", json={"token": token})
    assert response.status_code == 200, response.text


async def integration_key(
    client: AsyncClient, session_key: str, name: str = "backend"
) -> dict[str, Any]:
    created = await client.post(f"{ME}/api-keys", json={"name": name}, headers=headers(session_key))
    assert created.status_code == 201, created.text
    return created.json()


# --- Passwords ---


def test_password_hashing_round_trip() -> None:
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$")
    assert PASSWORD not in stored
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong password", stored)
    assert not verify_password(PASSWORD, None)
    assert not verify_password(PASSWORD, "garbage")
    assert hash_password(PASSWORD) != stored  # salted


# --- Register / login / logout ---


async def test_register_signs_in_and_asks_for_verification(client: AsyncClient) -> None:
    body = await register(client)

    assert body["tenant"]["email"] == "owner@acme.example"
    assert body["tenant"]["has_password"] is True
    assert body["tenant"]["email_verified"] is False
    assert body["tenant"]["domain_config"]["item_label"] == "job"
    assert body["verification_required"] is True
    assert body["api_key"].startswith("reco_")
    assert body["expires_at"]
    me = await client.get(ME, headers=headers(body["api_key"]))
    assert me.json()["id"] == body["tenant"]["id"]
    # No integration key exists until the owner creates one.
    assert (await client.get(f"{ME}/api-keys", headers=headers(body["api_key"]))).json() == []


@pytest.mark.parametrize(
    "overrides",
    [{"password": "short"}, {"email": "not-an-email"}, {"domain_type": "CUSTOM"}],
)
async def test_register_validation(client: AsyncClient, overrides: dict[str, Any]) -> None:
    response = await client.post(f"{AUTH}/register", json=registration(**overrides))
    assert response.status_code == 422


async def test_register_duplicate_email(client: AsyncClient) -> None:
    await register(client)
    again = await client.post(f"{AUTH}/register", json=registration(email="owner@acme.example"))
    assert again.status_code == 409


async def test_login_issues_session_key_hidden_from_key_list(client: AsyncClient) -> None:
    await register(client)

    response = await client.post(
        f"{AUTH}/login", json={"email": "OWNER@acme.example", "password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    session = headers(body["api_key"])
    assert body["expires_at"]
    assert (await client.get(ME, headers=session)).status_code == 200
    await verify(client)
    await integration_key(client, body["api_key"])
    keys = (await client.get(f"{ME}/api-keys", headers=session)).json()
    assert [k["name"] for k in keys] == ["backend"]


@pytest.mark.parametrize(
    ("email", "password"),
    [("owner@acme.example", "wrong password"), ("nobody@acme.example", PASSWORD)],
)
async def test_login_rejects_bad_credentials(
    client: AsyncClient, email: str, password: str
) -> None:
    await register(client)
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password"


async def test_login_is_rate_limited(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The per-minute cap, with the wrong-password lockout out of the way.
    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES", 100)
    await register(client)
    codes = [
        (
            await client.post(
                f"{AUTH}/login", json={"email": "owner@acme.example", "password": "nope-nope"}
            )
        ).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


async def test_tenant_without_password_cannot_log_in(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/tenants",
        json={"name": "Api", "email": "api@acme.example", "domain_type": "HR"},
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 201
    response = await client.post(
        f"{AUTH}/login", json={"email": "api@acme.example", "password": PASSWORD}
    )
    assert response.status_code == 401


async def test_logout_revokes_only_session_keys(client: AsyncClient) -> None:
    body = await register(client)
    await verify(client)
    integration = (await integration_key(client, body["api_key"]))["api_key"]
    session_key = (
        await client.post(
            f"{AUTH}/login", json={"email": "owner@acme.example", "password": PASSWORD}
        )
    ).json()["api_key"]

    assert (await client.post(f"{AUTH}/logout", headers=headers(session_key))).status_code == 204
    assert (await client.get(ME, headers=headers(session_key))).status_code == 401

    # Logging out with an integration key leaves it working.
    await client.post(f"{AUTH}/logout", headers=headers(integration))
    assert (await client.get(ME, headers=headers(integration))).status_code == 200


# --- API keys ---


async def test_create_and_revoke_api_keys(client: AsyncClient) -> None:
    key = headers((await register(client))["api_key"])
    await verify(client)

    created = await client.post(f"{ME}/api-keys", json={"name": "backend"}, headers=key)
    assert created.status_code == 201
    new_key = created.json()
    assert new_key["api_key"].startswith("reco_")

    revoked = await client.delete(f"{ME}/api-keys/{new_key['id']}", headers=key)
    assert revoked.status_code == 204
    keys = {
        k["name"]: k["is_active"] for k in (await client.get(f"{ME}/api-keys", headers=key)).json()
    }
    assert keys == {"backend": False}
    assert (await client.get(ME, headers=headers(new_key["api_key"]))).status_code == 401


async def test_cannot_revoke_another_tenants_key(client: AsyncClient) -> None:
    mine = headers((await register(client))["api_key"])
    other = await register(client, email="other@acme.example")
    await verify(client, "other@acme.example")
    other_key = await integration_key(client, other["api_key"])

    response = await client.delete(f"{ME}/api-keys/{other_key['id']}", headers=mine)

    assert response.status_code == 404


# --- Domain config ---


async def test_update_domain_config(client: AsyncClient) -> None:
    key = headers((await register(client))["api_key"])
    config = {
        "primary_embedding_field": "description",
        "searchable_fields": ["title", "description", "skills"],
        "filter_fields": ["location", "department", "employment_type"],
        "item_label": "role",
    }

    label_only = await client.put(
        f"{ME}/domain-config", json={"domain_config": config}, headers=key
    )
    config["filter_fields"] = ["location", "experience_years"]
    filters = await client.put(f"{ME}/domain-config", json={"domain_config": config}, headers=key)

    assert label_only.status_code == 200, label_only.text
    assert label_only.json()["rebuild_recommended"] is False
    assert label_only.json()["tenant"]["domain_config"]["item_label"] == "role"
    assert filters.json()["rebuild_recommended"] is True
    assert (await client.get(ME, headers=key)).json()["domain_config"] == config


async def test_invalid_domain_config_is_422(client: AsyncClient) -> None:
    key = headers((await register(client))["api_key"])
    bad = {"primary_embedding_field": "x", "searchable_fields": ["y"], "item_label": "job"}
    response = await client.put(f"{ME}/domain-config", json={"domain_config": bad}, headers=key)
    assert response.status_code == 422


# --- Account deletion ---


async def test_delete_account(
    client: AsyncClient,
    vector_store: FakeVectorStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await register(client)
    key = headers(body["api_key"])
    await client.post(
        "/api/v1/items/upload",
        json={"async": False, "items": [{"external_id": "j1", "description": "Python role"}]},
        headers=key,
    )
    assert vector_store.vectors(body["tenant"]["id"])

    wrong_email = await client.post(
        f"{ME}/delete", json={"confirm_email": "x@acme.example", "password": PASSWORD}, headers=key
    )
    wrong_password = await client.post(
        f"{ME}/delete",
        json={"confirm_email": "owner@acme.example", "password": "nope-nope"},
        headers=key,
    )
    deleted = await client.post(
        f"{ME}/delete",
        json={"confirm_email": "OWNER@acme.example", "password": PASSWORD},
        headers=key,
    )

    assert (wrong_email.status_code, wrong_password.status_code, deleted.status_code) == (
        400,
        401,
        204,
    )
    assert vector_store.vectors(body["tenant"]["id"]) == {}
    async with session_factory() as session:
        assert (await session.scalars(select(Tenant))).all() == []
    assert (await client.get(ME, headers=key)).status_code == 401


async def test_delete_account_keeps_everything_when_pinecone_is_down(
    client: AsyncClient, vector_store: FakeVectorStore
) -> None:
    key = headers((await register(client))["api_key"])
    vector_store.unavailable = True

    response = await client.post(
        f"{ME}/delete",
        json={"confirm_email": "owner@acme.example", "password": PASSWORD},
        headers=key,
    )

    assert response.status_code == 503
    assert (await client.get(ME, headers=key)).status_code == 200


async def test_session_keys_are_flagged(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = await register(client)
    await verify(client)
    await integration_key(client, body["api_key"])

    async with session_factory() as session:
        keys = (await session.scalars(select(ApiKey).order_by(ApiKey.created_at))).all()
    assert [(k.name, k.is_session, k.expires_at is not None) for k in keys] == [
        ("Dashboard session", True, True),
        ("backend", False, False),
    ]
