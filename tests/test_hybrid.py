from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.embedding import openai_embedder
from app.services.recommendation.hybrid import cosine, fuse, keyword_only_ids
from app.services.recommendation.keyword_search import KeywordMatch
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeVectorStore, fake_vector
from tests.test_recommend_endpoints import HR_CONFIG, JOBS, REC
from tests.test_reranker import _set_ranking


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


def _stored(vector_store: FakeVectorStore, auth: TenantAuth) -> dict[str, list[float]]:
    """Stored vectors by external id."""
    return {
        v["metadata"]["external_id"]: v["values"]
        for v in vector_store.vectors(auth.tenant_id).values()
    }


async def _results(client: AsyncClient, auth: TenantAuth, **payload: Any) -> list[dict[str, Any]]:
    response = await client.post(f"{REC}/by-text", json=payload, headers=auth.headers)
    assert response.status_code == 200, response.text
    return response.json()["results"]


# --- Through the API ---


async def test_an_exact_keyword_match_is_lifted_and_scored_by_similarity(
    client: AsyncClient,
    hr: TenantAuth,
    vector_store: FakeVectorStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stored = _stored(vector_store, hr)
    query = "spark"
    by_similarity = sorted(stored, key=lambda e: cosine(fake_vector(query), stored[e]))
    assert by_similarity[-1] != "job-3", "pick a query whose nearest vector is not job-3"

    plain = await _results(client, hr, query=query, top_k=1)
    await _set_ranking(session_factory, hr, keyword=1)
    hybrid = await _results(client, hr, query=query, top_k=1)

    assert plain[0]["external_id"] == by_similarity[-1]
    assert hybrid[0]["external_id"] == "job-3"
    # Found by keyword alone, yet its score is still its similarity to the query.
    assert hybrid[0]["score"] == round(cosine(fake_vector(query), stored["job-3"]), 4)
    assert vector_store.fetches[-1] == [
        pid
        for pid, v in vector_store.vectors(hr.tenant_id).items()
        if v["metadata"]["external_id"] == "job-3"
    ]


async def test_vector_only_by_default(
    client: AsyncClient, hr: TenantAuth, vector_store: FakeVectorStore
) -> None:
    await _results(client, hr, query="spark", top_k=1)
    assert vector_store.fetches == []


async def test_hybrid_respects_filters(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _set_ranking(session_factory, hr, keyword=1)

    results = await _results(
        client, hr, query="spark pipelines", top_k=3, filters={"location": "Remote"}
    )

    assert [r["external_id"] for r in results] == ["job-2"]


async def test_control_variant_and_similar_items_skip_keywords(
    client: AsyncClient,
    hr: TenantAuth,
    vector_store: FakeVectorStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _set_ranking(session_factory, hr, keyword=1, control_share=1)
    await _results(client, hr, query="spark", top_k=1)
    await _set_ranking(session_factory, hr, keyword=1)
    response = await client.post(
        f"{REC}/by-item", json={"external_id": "job-1", "top_k": 1}, headers=hr.headers
    )

    assert response.status_code == 200
    assert vector_store.fetches == []


async def test_batch_is_hybrid(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _set_ranking(session_factory, hr, keyword=1)

    body = (
        await client.post(
            f"{REC}/batch",
            json={
                "queries": [{"id": "a", "query": "spark"}, {"id": "b", "query": "react"}],
                "top_k": 1,
            },
            headers=hr.headers,
        )
    ).json()

    assert body["results"]["a"][0]["external_id"] == "job-3"
    assert body["results"]["b"][0]["external_id"] == "job-2"


# --- Fusion ---


def _vector(pid: str, score: float) -> dict[str, Any]:
    return {"id": pid, "score": score, "metadata": {"external_id": pid}}


def _keyword(pid: str, score: float) -> KeywordMatch:
    return KeywordMatch(external_id=pid, pinecone_id=pid, score=score, metadata={"city": "X"})


def test_fuse_adds_a_normalized_keyword_lift() -> None:
    fused = fuse(
        [_vector("a", 0.80), _vector("b", 0.70)],
        [_keyword("b", 0.4), _keyword("c", 0.2)],
        {"c": [1.0, 0.0]},
        [1.0, 0.0],
        weight=0.3,
        k=3,
    )

    by_id = {m["id"]: m for m in fused}
    assert [m["id"] for m in fused] == ["c", "b", "a"]
    assert by_id["b"]["retrieval_score"] == pytest.approx(0.70 + 0.3)  # best keyword hit
    assert by_id["c"]["retrieval_score"] == pytest.approx(1.0 + 0.15)  # half the best
    assert by_id["c"]["score"] == pytest.approx(1.0)  # its own similarity
    assert by_id["c"]["metadata"] == {"city": "X", "external_id": "c"}
    assert by_id["a"]["retrieval_score"] == pytest.approx(0.80)


def test_fuse_edge_cases() -> None:
    vectors = [_vector("a", 0.9), _vector("b", 0.5)]
    # Weight 0 keeps the vector order; keyword hits without a stored vector are dropped.
    fused = fuse(vectors, [_keyword("z", 1.0)], {}, [1.0], weight=0.0, k=5)
    assert [m["id"] for m in fused] == ["a", "b"]
    assert fuse([], [], {}, [1.0], weight=1.0, k=5) == []
    assert len(fuse(vectors, [], {}, [1.0], weight=1.0, k=1)) == 1
    assert keyword_only_ids(vectors, [_keyword("a", 1), _keyword("c", 1)]) == ["c"]
