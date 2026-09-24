"""Endpoints added for the dashboard: item search/detail/bulk delete, usage analytics, CORS."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.embedding import openai_embedder
from tests.conftest import TenantAuth
from tests.fakes import FakeVectorStore

ITEMS = "/api/v1/items"
JOBS: list[dict[str, Any]] = [
    {"external_id": "job-alpha", "description": "Python backend", "location": "Delhi"},
    {"external_id": "JOB-beta", "description": "React frontend", "location": "Remote"},
    {"external_id": "dish_1", "description": "Not a job", "location": "Pune"},
]


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def uploaded(client: AsyncClient, hr_tenant: TenantAuth) -> TenantAuth:
    response = await client.post(
        f"{ITEMS}/upload", json={"items": JOBS, "async": False}, headers=hr_tenant.headers
    )
    assert response.json()["succeeded"] == 3
    return hr_tenant


# --- Items ---


@pytest.mark.parametrize(
    ("search", "expected"),
    [("job", {"job-alpha", "JOB-beta"}), ("ALPHA", {"job-alpha"}), ("_", {"dish_1"}), ("%", set())],
)
async def test_search_items(
    client: AsyncClient, uploaded: TenantAuth, search: str, expected: set[str]
) -> None:
    response = await client.get(ITEMS, params={"search": search}, headers=uploaded.headers)
    assert {i["external_id"] for i in response.json()["items"]} == expected


async def test_get_item(client: AsyncClient, uploaded: TenantAuth) -> None:
    found = await client.get(f"{ITEMS}/job-alpha", headers=uploaded.headers)
    missing = await client.get(f"{ITEMS}/nope", headers=uploaded.headers)

    assert found.status_code == 200
    assert found.json()["raw_data"]["description"] == "Python backend"
    assert found.json()["embedding_status"] == "DONE"
    assert missing.status_code == 404


async def test_bulk_delete(
    client: AsyncClient, uploaded: TenantAuth, vector_store: FakeVectorStore
) -> None:
    response = await client.post(
        f"{ITEMS}/bulk-delete",
        json={"external_ids": ["job-alpha", "JOB-beta", "ghost"]},
        headers=uploaded.headers,
    )

    assert response.json() == {"deleted": 2, "not_found": ["ghost"]}
    remaining = (await client.get(ITEMS, headers=uploaded.headers)).json()["items"]
    assert [i["external_id"] for i in remaining] == ["dish_1"]
    assert len(vector_store.vectors(uploaded.tenant_id)) == 1


async def test_delete_all_items(
    client: AsyncClient, uploaded: TenantAuth, vector_store: FakeVectorStore
) -> None:
    response = await client.delete(ITEMS, headers=uploaded.headers)

    assert response.json() == {"deleted": 3, "not_found": []}
    assert (await client.get(ITEMS, headers=uploaded.headers)).json()["total"] == 0
    assert vector_store.vectors(uploaded.tenant_id) == {}


async def test_bulk_delete_keeps_items_when_pinecone_is_down(
    client: AsyncClient, uploaded: TenantAuth, vector_store: FakeVectorStore
) -> None:
    vector_store.unavailable = True
    response = await client.delete(ITEMS, headers=uploaded.headers)
    assert response.status_code == 503
    assert (await client.get(ITEMS, headers=uploaded.headers)).json()["total"] == 3


# --- Usage analytics ---


async def test_usage(client: AsyncClient, uploaded: TenantAuth) -> None:
    rec = "/api/v1/recommend"
    await client.post(f"{rec}/by-text", json={"query": "python"}, headers=uploaded.headers)
    await client.post(f"{rec}/by-text", json={"query": "python"}, headers=uploaded.headers)  # hit
    await client.post(f"{rec}/by-item", json={"external_id": "job-alpha"}, headers=uploaded.headers)
    raw = {"query": "x", "include_raw_data": True}
    await client.post(f"{rec}/by-text", json=raw, headers=uploaded.headers)  # bypass

    body = (
        await client.get("/api/v1/analytics/usage", params={"days": 7}, headers=uploaded.headers)
    ).json()

    assert body["days"] == 7
    assert body["total_recommendations"] == 4
    assert len(body["daily"]) == 7
    assert [d["date"] for d in body["daily"]] == [
        (datetime.now(UTC).date() - timedelta(days=6 - i)).isoformat() for i in range(7)
    ]
    assert body["daily"][-1]["count"] == 4
    assert body["daily"][0] == {
        "date": body["daily"][0]["date"],
        "count": 0,
        "avg_latency_ms": None,
    }
    assert body["by_query_type"] == {"TEXT": 3, "ITEM_ID": 1, "PROFILE": 0}
    # BYPASS is not cacheable: 1 hit out of 3 cacheable queries.
    assert body["cache_hit_rate"] == pytest.approx(0.3333)
    assert body["feedback_total"] == 0


async def test_usage_for_new_tenant(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    body = (await client.get("/api/v1/analytics/usage", headers=hr_tenant.headers)).json()
    assert body["total_recommendations"] == 0
    assert len(body["daily"]) == 30
    assert body["cache_hit_rate"] is None


# --- CORS ---


async def test_cors_exposes_dashboard_headers(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/items",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

    simple = await client.get("/health", headers={"Origin": "http://localhost:3000"})
    exposed = simple.headers["access-control-expose-headers"]
    assert {"X-Cache", "X-Request-ID", "Retry-After"} <= {h.strip() for h in exposed.split(",")}
