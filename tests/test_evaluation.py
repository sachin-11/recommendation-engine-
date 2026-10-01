import math
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import RecommendationLog, Tenant
from app.services.embedding import openai_embedder
from app.services.evaluation.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from tests.conftest import TenantAuth, register_tenant
from tests.test_recommend_endpoints import HR_CONFIG, JOBS
from tests.test_reranker import _set_ranking

EVAL = "/api/v1/evaluation"


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def hr(client: AsyncClient) -> TenantAuth:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    response = await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == 3, response.text
    return auth


async def _add(client: AsyncClient, auth: TenantAuth, query: str, relevant: dict[str, int]) -> Any:
    return await client.post(
        f"{EVAL}/queries", json={"query": query, "relevant": relevant}, headers=auth.headers
    )


# --- Metrics ---


def test_ndcg() -> None:
    relevant = {"a": 3, "b": 1}
    assert ndcg_at_k(["a", "b", "x"], relevant, 3) == pytest.approx(1.0)
    ideal = 7 + 1 / math.log2(3)
    assert ndcg_at_k(["b", "a"], relevant, 3) == pytest.approx((1 + 7 / math.log2(3)) / ideal)
    assert ndcg_at_k(["x", "y"], relevant, 3) == 0.0
    assert ndcg_at_k(["b", "a"], relevant, 1) == pytest.approx(1 / 7)  # only the top 1 counts
    assert ndcg_at_k(["a"], {}, 3) == 0.0


def test_recall_and_reciprocal_rank() -> None:
    relevant = {"a": 1, "b": 2, "c": 3, "d": 1}
    assert recall_at_k(["a", "x", "c"], relevant, 3) == pytest.approx(0.5)
    assert recall_at_k(["a", "x", "c"], relevant, 1) == pytest.approx(0.25)
    assert reciprocal_rank(["x", "y", "b"], relevant, 3) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x", "y", "b"], relevant, 2) == 0.0


# --- Golden set ---


async def test_golden_set_crud(client: AsyncClient, hr: TenantAuth) -> None:
    created = await _add(client, hr, "spark jobs", {"job-3": 3})
    replaced = await _add(client, hr, "spark jobs", {"job-3": 2, "job-1": 1})
    second = await _add(client, hr, "react", {"job-2": 3})

    assert (created.status_code, replaced.status_code, second.status_code) == (201, 200, 201)
    assert replaced.json()["id"] == created.json()["id"]
    listed = (await client.get(f"{EVAL}/queries", headers=hr.headers)).json()
    assert [(q["query"], q["relevant"]) for q in listed] == [
        ("spark jobs", {"job-3": 2, "job-1": 1}),
        ("react", {"job-2": 3}),
    ]

    other = await register_tenant(client, "other@acme.example", "HR", HR_CONFIG)
    foreign = await client.delete(f"{EVAL}/queries/{created.json()['id']}", headers=other.headers)
    deleted = await client.delete(f"{EVAL}/queries/{created.json()['id']}", headers=hr.headers)
    assert (foreign.status_code, deleted.status_code) == (404, 204)
    assert len((await client.get(f"{EVAL}/queries", headers=hr.headers)).json()) == 1
    assert (await client.get(f"{EVAL}/queries", headers=other.headers)).json() == []


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "x", "relevant": {}},
        {"query": "x", "relevant": {"job-1": 4}},
        {"query": "x", "relevant": {"job-1": 0}},
        {"query": "  ", "relevant": {"job-1": 1}},
        {"query": "x", "relevant": {"job-1": 1}, "extra": True},
    ],
)
async def test_invalid_golden_queries_are_422(
    client: AsyncClient, hr: TenantAuth, payload: dict[str, Any]
) -> None:
    response = await client.post(f"{EVAL}/queries", json=payload, headers=hr.headers)
    assert response.status_code == 422


# --- Runs ---


async def test_default_run_compares_vector_hybrid_and_current(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # "spark": job-3 is the only match, but not the nearest vector (see test_hybrid).
    await _add(client, hr, "spark", {"job-3": 3})
    # Saved: keyword matching on, but every user in the A/B control group.
    await _set_ranking(session_factory, hr, keyword=1, control_share=1)
    async with session_factory() as session:
        before = (await session.get(Tenant, uuid.UUID(hr.tenant_id))).domain_config  # type: ignore[union-attr]

    response = await client.post(f"{EVAL}/run", json={"k": 1}, headers=hr.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    by_name = {v["name"]: v for v in body["variants"]}
    assert list(by_name) == ["vector", "hybrid", "current"]
    assert (body["k"], body["queries"]) == (1, 1)
    assert by_name["vector"]["recall"] == 0.0
    assert by_name["hybrid"]["ranking"]["keyword"] == 1  # the saved weight
    assert by_name["hybrid"]["ranking"]["engagement"] == 0
    # The saved settings are evaluated as such, not as the A/B control.
    assert by_name["current"]["ranking"]["control_share"] == 0
    for name in ("hybrid", "current"):
        assert (by_name[name]["ndcg"], by_name[name]["recall"], by_name[name]["mrr"]) == (1, 1, 1)
    row = body["per_query"][0]
    assert row["top"]["hybrid"] == ["job-3"]
    assert row["by_variant"]["vector"]["recall"] == 0.0

    async with session_factory() as session:
        after = (await session.get(Tenant, uuid.UUID(hr.tenant_id))).domain_config  # type: ignore[union-attr]
        logged = await session.scalar(select(func.count()).select_from(RecommendationLog))
    assert after == before
    assert logged == 0


async def test_custom_variants(client: AsyncClient, hr: TenantAuth) -> None:
    await _add(client, hr, "spark", {"job-3": 3})
    variants = [
        {"name": "light", "ranking": {"keyword": 0.0}},
        {"name": "heavy", "ranking": {"keyword": 1.0}},
    ]

    body = (
        await client.post(f"{EVAL}/run", json={"k": 1, "variants": variants}, headers=hr.headers)
    ).json()

    assert [(v["name"], v["recall"]) for v in body["variants"]] == [("light", 0.0), ("heavy", 1.0)]


async def test_run_errors(client: AsyncClient, hr: TenantAuth) -> None:
    empty = await client.post(f"{EVAL}/run", json={}, headers=hr.headers)
    await _add(client, hr, "spark", {"job-3": 3})
    duplicate = await client.post(
        f"{EVAL}/run",
        json={"variants": [{"name": "a"}, {"name": "a"}]},
        headers=hr.headers,
    )
    bad = await client.post(
        f"{EVAL}/run",
        json={"variants": [{"name": "a", "ranking": {"keyword": 2}}]},
        headers=hr.headers,
    )

    assert (empty.status_code, duplicate.status_code, bad.status_code) == (400, 400, 422)
