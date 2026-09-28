"""Platform admin area: access, workspace list and detail, suspension, limits."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.user import User
from tests.conftest import TEST_PASSWORD, TenantAuth, register_tenant

ADMIN = "/api/v1/admin"


async def sign_in(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"X-API-Key": response.json()["api_key"]}


@pytest.fixture
async def operator(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> dict[str, str]:
    """A dashboard session of a platform admin, in a workspace of their own."""
    await register_tenant(client, "ops@platform.example", "FOOD")
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.email == "ops@platform.example").values(is_platform_admin=True)
        )
        await session.commit()
    return await sign_in(client, "ops@platform.example")


async def upload(client: AsyncClient, auth: TenantAuth, *ids: str) -> Any:
    items = [{"external_id": i, "description": f"Python role {i}"} for i in ids]
    return await client.post(
        "/api/v1/items/upload", json={"async": False, "items": items}, headers=auth.headers
    )


# --- Access ---


async def test_only_platform_admin_sessions_get_in(
    client: AsyncClient,
    hr_tenant: TenantAuth,
    operator: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A workspace owner, however senior in their workspace, is not a platform admin.
    owner = await sign_in(client, "hr@acme.example")
    assert (await client.get(f"{ADMIN}/workspaces", headers=owner)).status_code == 403
    assert (await client.get(f"{ADMIN}/workspaces")).status_code == 401

    # Integration keys are refused even when their workspace belongs to a platform admin.
    ops_key = await client.post("/api/v1/me/api-keys", json={"name": "ci"}, headers=operator)
    integration = {"X-API-Key": ops_key.json()["api_key"]}
    assert (await client.get(f"{ADMIN}/overview", headers=integration)).status_code == 403

    assert (await client.get(f"{ADMIN}/overview", headers=operator)).status_code == 200
    me = (await client.get("/api/v1/me", headers=operator)).json()
    assert me["user"]["is_platform_admin"] is True

    # Revoking takes effect on the next request of an existing session.
    async with session_factory() as session:
        await session.execute(update(User).values(is_platform_admin=False))
        await session.commit()
    assert (await client.get(f"{ADMIN}/overview", headers=operator)).status_code == 403


# --- Workspaces ---


async def test_list_search_and_sort(
    client: AsyncClient, hr_tenant: TenantAuth, food_tenant: TenantAuth, operator: dict[str, str]
) -> None:
    await upload(client, hr_tenant, "a", "b")
    await client.post(
        "/api/v1/recommend/by-text", json={"query": "python"}, headers=hr_tenant.headers
    )

    listing = (await client.get(f"{ADMIN}/workspaces", headers=operator)).json()
    assert listing["total"] == 3
    hr = next(w for w in listing["workspaces"] if w["id"] == hr_tenant.tenant_id)
    assert hr == {
        **hr,
        "email": "hr@acme.example",
        "status": "active",
        "owner_name": "hr",
        "members": 1,
        "items": 2,
        "queries_this_month": 1,
        "limits": {"max_items": None, "monthly_query_limit": None, "rate_limit_rpm": None},
    }
    assert hr["tokens_this_month"] > 0
    assert hr["last_active_at"] is not None

    by_items = await client.get(f"{ADMIN}/workspaces", params={"sort": "items"}, headers=operator)
    assert by_items.json()["workspaces"][0]["id"] == hr_tenant.tenant_id

    found = await client.get(f"{ADMIN}/workspaces", params={"search": "FOOD@"}, headers=operator)
    assert [w["id"] for w in found.json()["workspaces"]] == [food_tenant.tenant_id]

    paged = await client.get(
        f"{ADMIN}/workspaces", params={"page": 2, "page_size": 2}, headers=operator
    )
    assert paged.json() == {**paged.json(), "total": 3, "pages": 2, "page": 2}
    assert len(paged.json()["workspaces"]) == 1


async def test_workspace_detail(
    client: AsyncClient, hr_tenant: TenantAuth, operator: dict[str, str]
) -> None:
    await upload(client, hr_tenant, "a")

    detail = (
        await client.get(f"{ADMIN}/workspaces/{hr_tenant.tenant_id}", headers=operator)
    ).json()

    assert [m["email"] for m in detail["member_list"]] == ["hr@acme.example"]
    # Integration keys only: dashboard sessions are not listed.
    assert [k["name"] for k in detail["api_keys"]] == ["test"]
    assert detail["embedding_status"]["DONE"] == 1
    assert len(detail["queries_daily"]) == 30
    missing = await client.get(
        f"{ADMIN}/workspaces/00000000-0000-0000-0000-000000000000", headers=operator
    )
    assert missing.status_code == 404


async def test_overview(
    client: AsyncClient, hr_tenant: TenantAuth, food_tenant: TenantAuth, operator: dict[str, str]
) -> None:
    await upload(client, hr_tenant, "a")
    await client.post(
        "/api/v1/recommend/by-text", json={"query": "python"}, headers=hr_tenant.headers
    )

    overview = (await client.get(f"{ADMIN}/overview", headers=operator)).json()

    assert overview == {
        **overview,
        "workspaces_total": 3,
        "workspaces_active": 3,
        "workspaces_suspended": 0,
        "workspaces_new_last_30_days": 3,
        "users_total": 3,
        "items_total": 1,
        "queries_today": 1,
        "queries_this_month": 1,
    }
    assert overview["queries_daily"][-1]["count"] == 1
    assert [w["id"] for w in overview["top_workspaces"]] == [hr_tenant.tenant_id]


# --- Suspension ---


async def test_suspend_blocks_keys_sessions_and_sign_in(
    client: AsyncClient, hr_tenant: TenantAuth, operator: dict[str, str]
) -> None:
    session = await sign_in(client, "hr@acme.example")
    url = f"{ADMIN}/workspaces/{hr_tenant.tenant_id}"

    suspended = await client.post(
        f"{url}/suspend", json={"reason": "Invoice 42 unpaid"}, headers=operator
    )

    assert suspended.status_code == 200
    assert suspended.json() == {
        **suspended.json(),
        "status": "suspended",
        "suspended_reason": "Invoice 42 unpaid",
    }
    for headers in (hr_tenant.headers, session):
        blocked = await client.get("/api/v1/items", headers=headers)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "workspace_suspended"
        # The internal note never reaches the workspace.
        assert "Invoice" not in blocked.text
    login = await client.post(
        "/api/v1/auth/login", json={"email": "hr@acme.example", "password": TEST_PASSWORD}
    )
    assert login.json()["error"]["code"] == "workspace_suspended"

    activated = await client.post(f"{url}/activate", headers=operator)

    assert activated.json() == {
        **activated.json(),
        "status": "active",
        "suspended_at": None,
        "suspended_reason": None,
    }
    assert (await client.get("/api/v1/items", headers=hr_tenant.headers)).status_code == 200
    assert (await client.get("/api/v1/items", headers=session)).status_code == 200


async def test_cannot_suspend_own_workspace(client: AsyncClient, operator: dict[str, str]) -> None:
    me = (await client.get("/api/v1/me", headers=operator)).json()

    response = await client.post(
        f"{ADMIN}/workspaces/{me['id']}/suspend", json={"reason": "oops"}, headers=operator
    )

    assert response.status_code == 400
    assert (await client.get("/api/v1/me", headers=operator)).status_code == 200


# --- Limits ---


async def test_max_items_counts_only_new_items(
    client: AsyncClient, hr_tenant: TenantAuth, operator: dict[str, str]
) -> None:
    url = f"{ADMIN}/workspaces/{hr_tenant.tenant_id}/limits"
    assert (await client.patch(url, json={"max_items": 2}, headers=operator)).status_code == 200

    assert (await upload(client, hr_tenant, "a", "b")).status_code == 200
    # Re-uploading an existing item replaces it, so it fits.
    assert (await upload(client, hr_tenant, "a")).status_code == 200
    over = await upload(client, hr_tenant, "a", "c")
    assert over.status_code == 403
    assert over.json()["error"]["code"] == "limit_exceeded"

    # null restores "no cap"; fields not sent stay as they are.
    await client.patch(url, json={"rate_limit_rpm": 50}, headers=operator)
    reset = await client.patch(url, json={"max_items": None}, headers=operator)
    assert reset.json()["limits"] == {
        "max_items": None,
        "monthly_query_limit": None,
        "rate_limit_rpm": 50,
    }
    assert (await upload(client, hr_tenant, "c")).status_code == 200


async def test_monthly_query_limit(
    client: AsyncClient, hr_tenant: TenantAuth, operator: dict[str, str]
) -> None:
    await upload(client, hr_tenant, "a")
    await client.post(
        "/api/v1/recommend/by-text", json={"query": "python"}, headers=hr_tenant.headers
    )
    # One query already served this month counts towards the limit set afterwards.
    await client.patch(
        f"{ADMIN}/workspaces/{hr_tenant.tenant_id}/limits",
        json={"monthly_query_limit": 3},
        headers=operator,
    )

    def search(n: int) -> Any:
        if n == 1:
            return client.post(
                "/api/v1/recommend/by-text", json={"query": "python"}, headers=hr_tenant.headers
            )
        queries = [{"id": str(i), "query": "python"} for i in range(n)]
        return client.post(
            "/api/v1/recommend/batch", json={"queries": queries}, headers=hr_tenant.headers
        )

    assert (await search(1)).status_code == 200  # 2 of 3
    batch = await search(2)  # would make 4
    assert batch.status_code == 403
    assert batch.json()["error"]["code"] == "limit_exceeded"
    assert (await search(1)).status_code == 200  # the rejected batch was not counted
    assert (await search(1)).status_code == 403


async def test_rate_limit_override(
    client: AsyncClient,
    hr_tenant: TenantAuth,
    operator: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_RPM", 100)
    await client.patch(
        f"{ADMIN}/workspaces/{hr_tenant.tenant_id}/limits",
        json={"rate_limit_rpm": 2},
        headers=operator,
    )

    codes = [
        (await client.get("/api/v1/items", headers=hr_tenant.headers)).status_code for _ in range(3)
    ]

    assert codes == [200, 200, 429]


async def test_limits_validation(
    client: AsyncClient, hr_tenant: TenantAuth, operator: dict[str, str]
) -> None:
    url = f"{ADMIN}/workspaces/{hr_tenant.tenant_id}/limits"
    for body in ({"max_items": 0}, {"rate_limit_rpm": -1}, {"unknown": 1}):
        assert (await client.patch(url, json=body, headers=operator)).status_code == 422
