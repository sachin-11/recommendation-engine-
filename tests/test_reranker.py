import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ItemStats, RankingVariant, RecommendationLog, Tenant
from app.schemas.tenant import DomainConfig, RankingConfig, ranking_config
from app.services.embedding import openai_embedder
from app.services.recommendation.reranker import (
    MAX_CANDIDATES,
    Reranker,
    WorkspaceStats,
    candidate_count,
)
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeVectorStore
from tests.test_recommend_endpoints import HR_CONFIG, JOB1_TEXT, JOBS, REC

PRIOR = WorkspaceStats(
    impressions=3000, engagement=0.1, conversion=0.02, negative=0.05, max_impressions=1000
)


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


async def _set_ranking(
    session_factory: async_sessionmaker[AsyncSession], auth: TenantAuth, **ranking: Any
) -> None:
    # Validated like the settings endpoint would (that one needs an Admin session).
    config = DomainConfig.model_validate({**HR_CONFIG, "ranking": ranking})
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(auth.tenant_id))
        assert tenant is not None
        tenant.domain_config = config.model_dump(mode="json")
        await session.commit()


async def _seed_stats(
    session_factory: async_sessionmaker[AsyncSession],
    auth: TenantAuth,
    clicks: dict[str, float],
) -> None:
    """1000 impressions per job with the given clicks."""
    async with session_factory() as session:
        for item, n in clicks.items():
            session.add(
                ItemStats(
                    tenant_id=uuid.UUID(auth.tenant_id),
                    external_item_id=item,
                    impressions=1000.0,
                    clicks=n,
                    positives=0.0,
                    negatives=0.0,
                    conversions=0.0,
                )
            )
        await session.commit()


async def _variant(session_factory: async_sessionmaker[AsyncSession], query_id: str) -> str:
    async with session_factory() as session:
        entry = await session.get(RecommendationLog, uuid.UUID(query_id))
    assert entry is not None
    return entry.ranking_variant


# --- Through the API ---


async def test_feedback_reorders_results_but_keeps_similarity_scores(
    client: AsyncClient,
    hr: TenantAuth,
    vector_store: FakeVectorStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    plain = (
        await client.post(f"{REC}/by-text", json={"query": JOB1_TEXT}, headers=hr.headers)
    ).json()
    assert plain["results"][0]["external_id"] == "job-1"
    await _seed_stats(session_factory, hr, {"job-1": 50, "job-2": 900, "job-3": 50})
    await _set_ranking(session_factory, hr, engagement=5)

    body = (
        await client.post(
            f"{REC}/by-text", json={"query": JOB1_TEXT, "top_k": 2}, headers=hr.headers
        )
    ).json()

    assert [r["external_id"] for r in body["results"]] == ["job-2", "job-1"]
    assert [r["rank"] for r in body["results"]] == [1, 2]
    by_id = {r["external_id"]: r["score"] for r in plain["results"]}
    assert [r["score"] for r in body["results"]] == [by_id["job-2"], by_id["job-1"]]
    assert vector_store.queries[-1]["top_k"] == candidate_count(2)
    assert await _variant(session_factory, body["query_id"]) == RankingVariant.RERANKED


async def test_disabled_ranking_is_similarity_order(
    client: AsyncClient,
    hr: TenantAuth,
    vector_store: FakeVectorStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_stats(session_factory, hr, {"job-1": 50, "job-2": 900, "job-3": 50})
    await _set_ranking(session_factory, hr, enabled=False, engagement=5)

    body = (
        await client.post(
            f"{REC}/by-text", json={"query": JOB1_TEXT, "top_k": 2}, headers=hr.headers
        )
    ).json()

    assert body["results"][0]["external_id"] == "job-1"
    assert vector_store.queries[-1]["top_k"] == 2
    assert await _variant(session_factory, body["query_id"]) == RankingVariant.CONTROL


async def test_workspace_without_stats_fetches_only_top_k(
    client: AsyncClient, hr: TenantAuth, vector_store: FakeVectorStore
) -> None:
    body = (
        await client.post(
            f"{REC}/by-text", json={"query": JOB1_TEXT, "top_k": 2}, headers=hr.headers
        )
    ).json()

    assert body["results"][0]["external_id"] == "job-1"
    assert vector_store.queries[-1]["top_k"] == 2


async def test_by_item_and_batch_are_reranked(
    client: AsyncClient,
    hr: TenantAuth,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_stats(session_factory, hr, {"job-1": 900, "job-2": 0, "job-3": 0})
    await _set_ranking(session_factory, hr, engagement=5)

    similar = (
        await client.post(f"{REC}/by-item", json={"external_id": "job-3"}, headers=hr.headers)
    ).json()
    batch = (
        await client.post(
            f"{REC}/batch",
            json={"queries": [{"id": "a", "query": "react interfaces"}]},
            headers=hr.headers,
        )
    ).json()

    assert similar["results"][0]["external_id"] == "job-1"
    assert "job-3" not in [r["external_id"] for r in similar["results"]]
    assert batch["results"]["a"][0]["external_id"] == "job-1"
    assert await _variant(session_factory, batch["query_ids"]["a"]) == RankingVariant.RERANKED


async def test_changing_ranking_settings_bypasses_old_cache_entries(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    payload = {"query": JOB1_TEXT}
    await client.post(f"{REC}/by-text", json=payload, headers=hr.headers)
    await _set_ranking(session_factory, hr, enabled=False)

    response = await client.post(f"{REC}/by-text", json=payload, headers=hr.headers)

    assert response.headers["X-Cache"] == "MISS"


def test_invalid_ranking_weights_are_rejected() -> None:
    for ranking in ({"engagement": -1}, {"engagement": 6}, {"recency": 1}):
        with pytest.raises(ValidationError):
            DomainConfig.model_validate({**HR_CONFIG, "ranking": ranking})


def test_configs_saved_before_ranking_existed_get_defaults() -> None:
    assert ranking_config(HR_CONFIG) == RankingConfig()


# --- The scoring itself ---


def _item(**counts: float) -> ItemStats:
    base = {"impressions": 0.0, "clicks": 0.0, "positives": 0.0, "negatives": 0.0}
    return ItemStats(external_item_id="x", **{**base, "conversions": 0.0, **counts})


def test_items_without_history_score_their_similarity() -> None:
    config = RankingConfig(engagement=5, conversion=5, negative=5, popularity=5)
    assert Reranker._adjustment(None, config, PRIOR) == 0.0
    assert Reranker._adjustment(_item(), config, PRIOR) == 0.0


def test_an_average_item_is_not_moved() -> None:
    average = _item(impressions=500, clicks=50, conversions=10, negatives=25)
    assert Reranker._adjustment(average, RankingConfig(), PRIOR) == pytest.approx(0.0)


def test_little_evidence_moves_an_item_little() -> None:
    config = RankingConfig(engagement=1)
    lucky = Reranker._adjustment(_item(impressions=1, clicks=1), config, PRIOR)
    proven = Reranker._adjustment(_item(impressions=1000, clicks=1000), config, PRIOR)
    assert 0 < lucky < 0.05 < proven


def test_negative_feedback_lowers_and_popularity_raises() -> None:
    disliked = _item(impressions=1000, negatives=500)
    assert Reranker._adjustment(disliked, RankingConfig(), PRIOR) < 0
    popular = _item(impressions=1000, clicks=100, conversions=20, negatives=50)
    only_popularity = RankingConfig(engagement=0, conversion=0, negative=0, popularity=1)
    assert Reranker._adjustment(popular, only_popularity, PRIOR) == pytest.approx(1.0)


def test_feedback_beyond_impressions_is_capped() -> None:
    config = RankingConfig(engagement=1)
    capped = Reranker._adjustment(_item(impressions=10, clicks=500), config, PRIOR)
    full = Reranker._adjustment(_item(impressions=10, clicks=10), config, PRIOR)
    assert capped == pytest.approx(full)


def test_candidate_count() -> None:
    assert candidate_count(10) == 40
    assert candidate_count(100) == MAX_CANDIDATES
