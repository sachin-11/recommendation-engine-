from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.embedding import openai_embedder
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeOpenAIClient
from tests.test_recommend_endpoints import HR_CONFIG, JOBS, REC
from tests.test_reranker import _set_ranking

# The fake model's scores, by text in each job: Spark > React > the Python job.
SCORES = {"Spark": 9, "React": 5, "FastAPI": 1}


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
def chat(openai_client: FakeOpenAIClient) -> Any:
    completions = openai_client.chat.completions
    completions.scores = dict(SCORES)
    return completions


@pytest.fixture
async def hr(client: AsyncClient) -> TenantAuth:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    response = await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == 3, response.text
    return auth


async def _ask(client: AsyncClient, auth: TenantAuth, **payload: Any) -> dict[str, Any]:
    response = await client.post(
        f"{REC}/by-text", json={"query": "zzz", **payload}, headers=auth.headers
    )
    assert response.status_code == 200, response.text
    return {**response.json(), "cache": response.headers["X-Cache"]}


def _ids(body: dict[str, Any]) -> list[str]:
    return [r["external_id"] for r in body["results"]]


async def test_off_by_default(client: AsyncClient, hr: TenantAuth, chat: Any) -> None:
    body = await _ask(client, hr, top_k=3)

    assert chat.calls == []
    assert body["rerank_tokens"] == 0
    assert all("reason" not in r for r in body["results"])


async def test_the_model_orders_results_and_explains_them(
    client: AsyncClient,
    hr: TenantAuth,
    chat: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    plain = await _ask(client, hr, top_k=3)
    await _set_ranking(session_factory, hr, llm_rerank=True)

    body = await _ask(client, hr, top_k=3)

    assert _ids(body) == ["job-3", "job-2", "job-1"]
    assert [r["rank"] for r in body["results"]] == [1, 2, 3]
    assert body["results"][0]["reason"] == "matches 3 words"
    assert body["rerank_tokens"] > 0
    # Scores are still the similarities.
    similarity = {r["external_id"]: r["score"] for r in plain["results"]}
    assert all(r["score"] == similarity[r["external_id"]] for r in body["results"])
    # The model read each job's text, labelled by field.
    prompt = chat.calls[0]["messages"][1]["content"]
    assert "description: Spark pipelines" in prompt and 'Query: "zzz"' in prompt


async def test_the_model_judges_more_candidates_than_it_returns(
    client: AsyncClient,
    hr: TenantAuth,
    chat: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _set_ranking(session_factory, hr, llm_rerank=True, llm_candidates=3)

    body = await _ask(client, hr, top_k=1)

    assert _ids(body) == ["job-3"]
    assert len(chat.calls) == 1


async def test_only_the_top_candidates_are_judged(
    client: AsyncClient,
    hr: TenantAuth,
    chat: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    plain = _ids(await _ask(client, hr, top_k=3))
    await _set_ranking(session_factory, hr, llm_rerank=True, llm_candidates=2)

    body = await _ask(client, hr, top_k=3)

    judged = sorted(plain[:2], key=lambda i: -{"job-3": 9, "job-2": 5, "job-1": 1}[i])
    assert _ids(body) == [*judged, plain[2]]
    assert "reason" not in body["results"][2]


async def test_answers_are_cached_with_the_results(
    client: AsyncClient,
    hr: TenantAuth,
    chat: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _set_ranking(session_factory, hr, llm_rerank=True)

    first = await _ask(client, hr, top_k=3)
    second = await _ask(client, hr, top_k=3)

    assert (first["cache"], second["cache"]) == ("MISS", "HIT")
    assert second["rerank_tokens"] == 0
    assert second["results"] == first["results"]
    assert len(chat.calls) == 1


async def test_a_failing_model_keeps_the_previous_order(
    client: AsyncClient,
    hr: TenantAuth,
    chat: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    plain = _ids(await _ask(client, hr, top_k=3, filters={"location": "Delhi"}))
    await _set_ranking(session_factory, hr, llm_rerank=True)
    chat.down = True

    body = await _ask(client, hr, top_k=3, filters={"location": "Delhi"})

    assert _ids(body) == plain
    assert all("reason" not in r for r in body["results"])


async def test_where_the_stage_does_not_run(
    client: AsyncClient,
    hr: TenantAuth,
    chat: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The A/B control group, and similar-item queries, never call the model.
    await _set_ranking(session_factory, hr, llm_rerank=True, control_share=1)
    await _ask(client, hr, top_k=3)
    await _set_ranking(session_factory, hr, llm_rerank=True)
    response = await client.post(
        f"{REC}/by-item", json={"external_id": "job-1", "top_k": 2}, headers=hr.headers
    )

    assert response.status_code == 200
    assert chat.calls == []


async def test_profile_and_batch_queries(
    client: AsyncClient,
    hr: TenantAuth,
    chat: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _set_ranking(session_factory, hr, llm_rerank=True)

    profile = (
        await client.post(
            f"{REC}/by-profile",
            json={"profile": {"skills": "anything"}, "top_k": 3},
            headers=hr.headers,
        )
    ).json()
    batch = (
        await client.post(
            f"{REC}/batch",
            json={"queries": [{"id": "a", "query": "q1"}, {"id": "b", "query": "q2"}], "top_k": 2},
            headers=hr.headers,
        )
    ).json()

    assert _ids(profile) == ["job-3", "job-2", "job-1"]
    assert [r["external_id"] for r in batch["results"]["a"]] == ["job-3", "job-2"]
    assert batch["results"]["b"][0]["reason"]
    assert batch["rerank_tokens"] > 0
    assert len(chat.calls) == 3  # one per query, the batch's run concurrently
