"""OpenAI token accounting: per upload, per query, per tenant, with a cost estimate."""

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services.embedding import openai_embedder
from app.services.embedding.text_builder import TextBuilder
from tests.conftest import HR_CONFIG, TenantAuth, register_tenant
from tests.fakes import FakeOpenAIClient, fake_tokens

JOBS: list[dict[str, Any]] = [
    {"external_id": "j1", "title": "Backend Engineer", "description": "Python APIs with FastAPI"},
    {"external_id": "j2", "title": "Data Scientist", "description": "Train ranking models"},
    {"external_id": "j3", "title": "Designer", "description": "Design mobile screens"},
]
INGEST_TOKENS = fake_tokens([TextBuilder().build_embedding_text(j, HR_CONFIG) for j in JOBS])
REC = "/api/v1/recommend"


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


async def upload(client: AsyncClient, auth: TenantAuth, items: list[dict[str, Any]]) -> None:
    response = await client.post(
        "/api/v1/items/upload", json={"items": items, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == len(items), response.text


async def token_usage(client: AsyncClient, auth: TenantAuth, days: int = 30) -> dict[str, Any]:
    response = await client.get(
        "/api/v1/analytics/tokens", params={"days": days}, headers=auth.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_upload_and_query_tokens_are_counted(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    await upload(client, hr_tenant, JOBS)
    query = "senior python developer"

    first = await client.post(f"{REC}/by-text", json={"query": query}, headers=hr_tenant.headers)
    cached = await client.post(f"{REC}/by-text", json={"query": query}, headers=hr_tenant.headers)
    by_item = await client.post(
        f"{REC}/by-item", json={"external_id": "j1"}, headers=hr_tenant.headers
    )

    assert first.json()["embedding_tokens"] == fake_tokens([query]) == 3
    assert cached.headers["X-Cache"] == "HIT" and cached.json()["embedding_tokens"] == 0
    # by-item reuses the stored vector: no embedding, no tokens.
    assert by_item.json()["embedding_tokens"] == 0

    usage = await token_usage(client, hr_tenant, days=7)
    assert usage["by_source"] == {"INGEST": INGEST_TOKENS, "QUERY": 3}
    assert usage["total_tokens"] == INGEST_TOKENS + 3
    assert usage["api_calls"] == 2
    assert usage["model"] == settings.EMBEDDING_MODEL
    assert len(usage["daily"]) == 7
    assert usage["daily"][-1] == {
        "date": usage["daily"][-1]["date"],
        "ingest_tokens": INGEST_TOKENS,
        "query_tokens": 3,
    }


async def test_reupload_hits_the_embedding_cache(
    client: AsyncClient, hr_tenant: TenantAuth, openai_client: FakeOpenAIClient
) -> None:
    await upload(client, hr_tenant, JOBS)
    await upload(client, hr_tenant, JOBS)  # identical text: served from Redis

    usage = await token_usage(client, hr_tenant)

    assert usage["by_source"]["INGEST"] == INGEST_TOKENS
    assert usage["texts_embedded"] == 6
    assert usage["cache_hits"] == 3
    assert len(openai_client.embeddings.calls) == 1


async def test_batch_reports_total_tokens(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    await upload(client, hr_tenant, JOBS)
    queries = [{"id": "a", "query": "python developer"}, {"id": "b", "query": "ml engineer role"}]

    response = await client.post(
        f"{REC}/batch", json={"queries": queries}, headers=hr_tenant.headers
    )

    assert response.json()["embedding_tokens"] == 5
    assert (await token_usage(client, hr_tenant))["by_source"]["QUERY"] == 5


async def test_estimated_cost(
    client: AsyncClient, hr_tenant: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_PRICE_PER_MILLION_TOKENS", 2_000_000.0)
    await upload(client, hr_tenant, JOBS)

    usage = await token_usage(client, hr_tenant)

    # 2 USD per token (absurd price, easy arithmetic).
    assert usage["price_per_million_tokens"] == 2_000_000.0
    assert usage["estimated_cost_usd"] == pytest.approx(INGEST_TOKENS * 2)


async def test_usage_is_per_tenant(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    other = await register_tenant(client, "other@acme.example")
    await upload(client, hr_tenant, JOBS)

    usage = await token_usage(client, other)

    assert usage["total_tokens"] == 0
    assert usage["estimated_cost_usd"] == 0


async def test_token_metric(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    await upload(client, hr_tenant, JOBS[:1])
    body = (await client.get("/metrics")).text
    assert 'openai_embedding_tokens_total{model="text-embedding-3-small",source="INGEST"}' in body
