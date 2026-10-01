import json
import uuid
from typing import Any

import fakeredis
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import QueryType, RecommendationLog
from app.services.embedding import openai_embedder
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeOpenAIClient
from tests.test_recommend_endpoints import HR_CONFIG, JOBS, REC


def _answer(search_text: str, *conditions: tuple[str, str, list[str], float | None]) -> str:
    return json.dumps(
        {
            "search_text": search_text,
            "filters": [
                {"field": f, "op": op, "text_values": values, "number": number}
                for f, op, values, number in conditions
            ],
        }
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


async def _ask(client: AsyncClient, auth: TenantAuth, question: str, **extra: Any) -> Any:
    return await client.post(
        f"{REC}/ask", json={"question": question, **extra}, headers=auth.headers
    )


async def test_a_question_becomes_a_filtered_search(
    client: AsyncClient,
    hr: TenantAuth,
    openai_client: FakeOpenAIClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    openai_client.chat.completions.content = _answer(
        "engineer",
        ("location", "eq", ["delhi"], None),
        ("experience_years", "gte", [], 6),
        ("salary", "gte", [], 100),
    )

    response = await _ask(
        client, hr, "a senior engineer job in Delhi, no salary under 100k", user_id="u1"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["external_id"] for r in body["results"]] == ["job-3"]
    assert body["interpretation"] == {
        "search_text": "engineer",
        "filters": {"location": "Delhi", "experience_years": {"gte": 6}},
        "ignored": ["salary: not a filter field"],
    }
    assert body["relaxed"] is False
    assert body["understand_tokens"] > 0
    async with session_factory() as session:
        entry = await session.get(RecommendationLog, uuid.UUID(body["query_id"]))
    assert entry is not None
    assert entry.query_type is QueryType.ASK
    assert entry.query_input == {
        "question": "a senior engineer job in Delhi, no salary under 100k",
        "query": "engineer",
    }
    assert entry.filters_applied == {"location": "Delhi", "experience_years": {"gte": 6}}
    assert entry.end_user_id == "u1"


async def test_filters_that_match_nothing_are_relaxed(
    client: AsyncClient, hr: TenantAuth, openai_client: FakeOpenAIClient
) -> None:
    # Remote jobs exist, and jobs with 7 years exist, but not both at once.
    openai_client.chat.completions.content = _answer(
        "engineer", ("location", "eq", ["Remote"], None), ("experience_years", "gte", [], 7)
    )

    body = (await _ask(client, hr, "remote, 7+ years")).json()

    assert body["relaxed"] is True
    assert len(body["results"]) == 3


async def test_an_unreadable_question_is_searched_as_written(
    client: AsyncClient, hr: TenantAuth, openai_client: FakeOpenAIClient
) -> None:
    openai_client.chat.completions.down = True

    response = await _ask(client, hr, "spark pipelines in Delhi")

    assert response.status_code == 200
    body = response.json()
    assert body["interpretation"] == {
        "search_text": "spark pipelines in Delhi",
        "filters": {},
        "ignored": [],
        "fallback": "error",
    }
    assert body["results"]


async def test_the_catalogues_values_are_cached(
    client: AsyncClient,
    hr: TenantAuth,
    openai_client: FakeOpenAIClient,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    openai_client.chat.completions.content = _answer("engineer")
    await _ask(client, hr, "anything")

    cached = json.loads(await redis.get(f"profiles:{hr.tenant_id}"))
    assert cached["location"]["kind"] == "text"
    assert set(cached["location"]["values"]) == {"Delhi", "Remote"}


@pytest.mark.parametrize("payload", [{"question": "  "}, {"question": "x", "filters": {}}, {}])
async def test_invalid_requests_are_422(
    client: AsyncClient, hr: TenantAuth, payload: dict[str, Any]
) -> None:
    response = await client.post(f"{REC}/ask", json=payload, headers=hr.headers)
    assert response.status_code == 422


async def test_requires_an_api_key(client: AsyncClient) -> None:
    await register_tenant(client, "x@acme.example")
    response = await client.post(f"{REC}/ask", json={"question": "x"})
    assert response.status_code == 401
