import json
import uuid
from typing import Any

import fakeredis
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Tenant
from app.services.embedding import openai_embedder
from app.services.recommendation.query_understanding import (
    FieldProfile,
    QueryUnderstanding,
    profile_fields,
    to_filters,
)
from tests.conftest import register_tenant
from tests.fakes import FakeOpenAIClient
from tests.test_recommend_endpoints import HR_CONFIG, JOBS

PROFILES = {
    "location": FieldProfile("text", values=["Delhi", "Remote", "Pune"]),
    "job_type": FieldProfile("text", values=["full_time", "contract"]),
    "experience_years": FieldProfile("number", minimum=2, maximum=7),
}


def _cond(
    field: str, op: str, values: list[str] | None = None, number: float | None = None
) -> dict[str, Any]:
    return {"field": field, "op": op, "text_values": values or [], "number": number}


def _answer(search_text: str, *conditions: dict[str, Any]) -> str:
    return json.dumps({"search_text": search_text, "filters": list(conditions)})


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
def understanding(
    openai_client: FakeOpenAIClient, redis: fakeredis.FakeAsyncRedis
) -> QueryUnderstanding:
    return QueryUnderstanding(openai_client, redis, model="test-model", timeout=0.5)  # type: ignore[arg-type]


# --- Checking the model's filters ---


def test_text_values_are_matched_to_the_catalogue() -> None:
    filters, ignored = to_filters(
        [
            _cond("location", "in", ["delhi", " PUNE ", "Delhi"]),
            _cond("job_type", "eq", ["contract"]),
        ],
        PROFILES,
    )
    assert filters == {"location": ["Delhi", "Pune"], "job_type": "contract"}
    assert ignored == []


def test_number_ranges_combine() -> None:
    filters, ignored = to_filters(
        [_cond("experience_years", "gte", number=3), _cond("experience_years", "lte", number=5)],
        PROFILES,
    )
    assert filters == {"experience_years": {"gte": 3, "lte": 5}}
    assert ignored == []


def test_what_the_catalogue_cannot_support_is_dropped_and_reported() -> None:
    filters, ignored = to_filters(
        [
            _cond("salary", "gte", number=100),  # not a filter field
            _cond("location", "eq", ["Mumbai"]),  # no item has it
            _cond("location", "gte", number=3),  # a range on text
            _cond("experience_years", "gte", ["three"]),  # no number
            _cond("job_type", "nin", ["contract"]),
            _cond("job_type", "eq", ["full_time"]),  # a second condition on one field
        ],
        PROFILES,
    )
    assert filters == {"job_type": {"nin": ["contract"]}}
    assert ignored == [
        "salary: not a filter field",
        "location: no items with 'Mumbai'",
        "location: is text, not a number",
        "experience_years: needs a number range",
        "job_type: more than one condition",
    ]


def test_a_field_without_known_values_takes_them_as_given() -> None:
    filters, _ = to_filters([_cond("city", "eq", ["Goa"])], {"city": FieldProfile("text")})
    assert filters == {"city": "Goa"}


async def test_fields_are_profiled_from_the_items(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    await client.post(
        "/api/v1/items/upload", json={"items": JOBS, "async": False}, headers=auth.headers
    )
    async with session_factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(auth.tenant_id))
        assert tenant is not None
        profiles = await profile_fields(session, tenant)

    assert profiles["location"].kind == "text"
    assert profiles["location"].values[0] == "Delhi"  # the most common first
    assert set(profiles["location"].values) == {"Delhi", "Remote"}
    assert (profiles["experience_years"].minimum, profiles["experience_years"].maximum) == (2, 7)


# --- Asking the model ---


async def test_interpret(
    understanding: QueryUnderstanding, openai_client: FakeOpenAIClient
) -> None:
    chat = openai_client.chat.completions
    chat.content = _answer(
        "python backend developer",
        _cond("location", "in", ["remote", "Pune"]),
        _cond("experience_years", "lte", number=5),
        _cond("visa", "eq", ["yes"]),
    )

    result = await understanding.interpret(
        "remote python job, under 5 years, Pune ok", PROFILES, HR_CONFIG
    )

    assert result.search_text == "python backend developer"
    assert result.filters == {"location": ["Remote", "Pune"], "experience_years": {"lte": 5}}
    assert result.ignored == ["visa: not a filter field"]
    assert result.fallback is None and result.cost_usd > 0
    # The model sees the fields with their real values, and the question as data.
    sent = json.loads(chat.calls[0]["messages"][1]["content"])
    assert sent["filter_fields"]["location"] == {
        "type": "text",
        "values": ["Delhi", "Remote", "Pune"],
    }
    assert sent["request"] == "remote python job, under 5 years, Pune ok"


async def test_answers_are_cached(
    understanding: QueryUnderstanding, openai_client: FakeOpenAIClient
) -> None:
    openai_client.chat.completions.content = _answer("python")
    await understanding.interpret("python jobs", PROFILES, HR_CONFIG)
    second = await understanding.interpret("python jobs", PROFILES, HR_CONFIG)

    assert second.cached and second.search_text == "python"
    assert len(openai_client.chat.completions.calls) == 1


@pytest.mark.parametrize(
    ("setup", "fallback"),
    [
        ({"delay": 2.0}, "timeout"),
        ({"down": True}, "error"),
        ({"content": "nope"}, "error"),
        ({"content": json.dumps({"search_text": "x"})}, "error"),
    ],
)
async def test_failures_search_for_the_question_as_written(
    understanding: QueryUnderstanding,
    openai_client: FakeOpenAIClient,
    setup: dict[str, Any],
    fallback: str,
) -> None:
    for name, value in setup.items():
        setattr(openai_client.chat.completions, name, value)

    result = await understanding.interpret("remote python job", PROFILES, HR_CONFIG)

    assert (result.search_text, result.filters, result.fallback) == (
        "remote python job",
        {},
        fallback,
    )


async def test_an_empty_search_text_falls_back_to_the_question(
    understanding: QueryUnderstanding, openai_client: FakeOpenAIClient
) -> None:
    openai_client.chat.completions.content = _answer("  ", _cond("location", "eq", ["Remote"]))
    result = await understanding.interpret("anything remote", PROFILES, HR_CONFIG)
    assert (result.search_text, result.filters) == ("anything remote", {"location": "Remote"})


async def test_without_a_client() -> None:
    result = await QueryUnderstanding(None).interpret("q", PROFILES, HR_CONFIG)
    assert (result.search_text, result.fallback) == ("q", "not_configured")
