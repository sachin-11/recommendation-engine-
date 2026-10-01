import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import QueryType, RecommendationLog
from app.services.embedding import openai_embedder
from app.services.recommendation.dependencies import get_summarizer
from app.services.recommendation.summarizer import Summarizer
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeOpenAIClient
from tests.test_ask import _answer
from tests.test_recommend_endpoints import HR_CONFIG, JOBS, REC


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def hr(client: AsyncClient, openai_client: FakeOpenAIClient) -> TenantAuth:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    response = await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == 3, response.text
    # The question is read as a search for engineers in Delhi.
    openai_client.chat.completions.content = _answer(
        "engineer", ("location", "eq", ["Delhi"], None)
    )
    return auth


def parse_events(text: str) -> list[tuple[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def _stream(
    client: AsyncClient, auth: TenantAuth, question: str = "engineers in Delhi"
) -> Any:
    return await client.post(
        f"{REC}/ask/stream", json={"question": question, "top_k": 3}, headers=auth.headers
    )


async def test_events_arrive_in_order(
    client: AsyncClient,
    hr: TenantAuth,
    openai_client: FakeOpenAIClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await _stream(client, hr)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_events(response.text)
    names = [name for name, _ in events]
    assert names[0:2] == ["interpretation", "results"] and names[-1] == "done"
    assert set(names[2:-1]) == {"summary"}

    interpretation, results = events[0][1], events[1][1]
    assert interpretation == {
        "search_text": "engineer",
        "filters": {"location": "Delhi"},
        "ignored": [],
    }
    assert {r["external_id"] for r in results["results"]} == {"job-1", "job-3"}
    assert "interpretation" not in results and results["relaxed"] is False
    summary = "".join(data["text"] for name, data in events if name == "summary")
    assert summary.strip() == "Found 3 jobs. The data engineer role fits best."
    assert events[-1][1]["summary_tokens"] > 0

    # The summarizer read the results' text, with their ids.
    sent = json.loads(openai_client.chat.completions.calls[-1]["messages"][1]["content"])
    assert sent["question"] == "engineers in Delhi"
    assert {r["id"] for r in sent["results"]} == {"job-1", "job-3"}
    assert all("title:" in r["text"] and "location: Delhi" in r["text"] for r in sent["results"])
    # Logged like /ask.
    async with session_factory() as session:
        entry = await session.get(RecommendationLog, uuid.UUID(results["query_id"]))
    assert entry is not None and entry.query_type is QueryType.ASK


async def test_a_failing_summary_keeps_the_results(
    client: AsyncClient, hr: TenantAuth, openai_client: FakeOpenAIClient
) -> None:
    openai_client.chat.completions.stream_fail_after = 2

    events = parse_events((await _stream(client, hr)).text)

    names = [name for name, _ in events]
    assert names == ["interpretation", "results", "summary", "summary", "error"]
    assert events[1][1]["results"]


async def test_without_a_model_there_is_no_summary(client: AsyncClient, hr: TenantAuth) -> None:
    client._transport.app.dependency_overrides[get_summarizer] = lambda: Summarizer(None)  # type: ignore[attr-defined]

    events = parse_events((await _stream(client, hr)).text)

    assert [name for name, _ in events] == ["interpretation", "results", "done"]
    assert events[-1][1] == {"summary_tokens": 0, "summary_cost_usd": 0.0}


async def test_errors_before_streaming_are_plain_http_errors(
    client: AsyncClient, hr: TenantAuth
) -> None:
    bad = await client.post(f"{REC}/ask/stream", json={"question": ""}, headers=hr.headers)
    anonymous = await client.post(f"{REC}/ask/stream", json={"question": "x"})
    assert (bad.status_code, anonymous.status_code) == (422, 401)
