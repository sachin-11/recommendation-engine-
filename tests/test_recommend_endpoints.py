import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import QueryType, RecommendationLog, UserFeedback
from app.services.embedding import openai_embedder
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeOpenAIClient, FakeVectorStore

REC = "/api/v1/recommend"

HR_CONFIG: dict[str, Any] = {
    "primary_embedding_field": "description",
    "searchable_fields": ["title", "description", "skills"],
    "filter_fields": ["location", "experience_years", "job_type"],
    "item_label": "job",
}
JOBS: list[dict[str, Any]] = [
    {
        "external_id": "job-1",
        "title": "Backend Engineer",
        "description": "Python FastAPI services",
        "skills": ["Python", "FastAPI"],
        "location": "Delhi",
        "experience_years": 5,
        "job_type": "full_time",
    },
    {
        "external_id": "job-2",
        "title": "Frontend Engineer",
        "description": "React interfaces",
        "location": "Remote",
        "experience_years": 2,
        "job_type": "contract",
    },
    {
        "external_id": "job-3",
        "title": "Data Engineer",
        "description": "Spark pipelines",
        "location": "Delhi",
        "experience_years": 7,
        "job_type": "full_time",
    },
]
JOB1_TEXT = "description: Python FastAPI services title: Backend Engineer skills: Python FastAPI"


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


# --- The three query types ---


async def test_by_text(client: AsyncClient, hr: TenantAuth) -> None:
    response = await client.post(
        f"{REC}/by-text", json={"query": JOB1_TEXT, "top_k": 2}, headers=hr.headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["results"][0] == {
        "rank": 1,
        "external_id": "job-1",
        "score": 1.0,
        "score_label": "Excellent Match",
        "metadata": {"location": "Delhi", "experience_years": 5, "job_type": "full_time"},
    }
    uuid.UUID(body["query_id"])
    assert body["latency_ms"] >= 0
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["X-Cache"] == "MISS"


async def test_by_text_with_filters(client: AsyncClient, hr: TenantAuth) -> None:
    response = await client.post(
        f"{REC}/by-text",
        json={
            "query": "engineer",
            "filters": {"location": "Delhi", "experience_years": {"gte": 6}},
        },
        headers=hr.headers,
    )

    assert [r["external_id"] for r in response.json()["results"]] == ["job-3"]


async def test_by_text_with_raw_data(client: AsyncClient, hr: TenantAuth) -> None:
    response = await client.post(
        f"{REC}/by-text",
        json={"query": JOB1_TEXT, "top_k": 1, "include_raw_data": True},
        headers=hr.headers,
    )

    assert response.json()["results"][0]["raw_data"]["title"] == "Backend Engineer"
    assert response.headers["X-Cache"] == "BYPASS"


async def test_by_item(client: AsyncClient, hr: TenantAuth) -> None:
    response = await client.post(
        f"{REC}/by-item", json={"external_id": "job-1", "top_k": 5}, headers=hr.headers
    )

    assert response.status_code == 200, response.text
    ids = [r["external_id"] for r in response.json()["results"]]
    assert sorted(ids) == ["job-2", "job-3"]


async def test_by_item_errors(client: AsyncClient, hr: TenantAuth) -> None:
    missing = await client.post(f"{REC}/by-item", json={"external_id": "x"}, headers=hr.headers)
    assert missing.status_code == 404


async def test_by_profile(client: AsyncClient, hr: TenantAuth) -> None:
    response = await client.post(
        f"{REC}/by-profile",
        json={
            "profile": {
                "skills": "Python, FastAPI, PostgreSQL",
                "experience": "5 years backend development",
                "preferred_location": "Remote",
            },
            "top_k": 3,
            "filters": {"job_type": "full_time"},
        },
        headers=hr.headers,
    )

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert {r["external_id"] for r in results} == {"job-1", "job-3"}


async def test_batch(client: AsyncClient, hr: TenantAuth) -> None:
    response = await client.post(
        f"{REC}/batch",
        json={
            "queries": [
                {"id": "q1", "query": JOB1_TEXT},
                {"id": "q2", "query": "react", "filters": {"location": "Remote"}},
            ],
            "top_k": 1,
        },
        headers=hr.headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"]["q1"][0]["external_id"] == "job-1"
    assert body["results"]["q2"][0]["external_id"] == "job-2"
    assert set(body["query_ids"]) == {"q1", "q2"}
    assert response.headers["X-Cache"] == "MISS"


async def test_batch_limits(client: AsyncClient, hr: TenantAuth) -> None:
    too_many = [{"id": f"q{i}", "query": "x"} for i in range(21)]
    duplicate = [{"id": "q", "query": "a"}, {"id": "q", "query": "b"}]

    for queries in (too_many, duplicate, []):
        response = await client.post(f"{REC}/batch", json={"queries": queries}, headers=hr.headers)
        assert response.status_code == 422


# --- Errors ---


async def test_unknown_filter_is_400(client: AsyncClient, hr: TenantAuth) -> None:
    response = await client.post(
        f"{REC}/by-text", json={"query": "x", "filters": {"salary": 5}}, headers=hr.headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "salary"


async def test_pinecone_timeout_is_503_with_retry_after(
    client: AsyncClient, hr: TenantAuth, vector_store: FakeVectorStore
) -> None:
    vector_store.query_times_out = True

    response = await client.post(f"{REC}/by-text", json={"query": "python"}, headers=hr.headers)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert "timed out" in response.json()["error"]["message"]
    assert "results" not in response.json()


async def test_openai_down_is_503(
    client: AsyncClient, hr: TenantAuth, openai_client: FakeOpenAIClient
) -> None:
    openai_client.embeddings.down = True
    response = await client.post(f"{REC}/by-text", json={"query": "brand new"}, headers=hr.headers)
    assert response.status_code == 503


async def test_top_k_bounds(client: AsyncClient, hr: TenantAuth) -> None:
    for top_k in (0, 101):
        response = await client.post(
            f"{REC}/by-text", json={"query": "x", "top_k": top_k}, headers=hr.headers
        )
        assert response.status_code == 422


async def test_requires_api_key(client: AsyncClient) -> None:
    response = await client.post(f"{REC}/by-text", json={"query": "x"})
    assert response.status_code == 401
    assert "X-Request-ID" in response.headers


async def test_tenant_without_items_gets_empty_results(client: AsyncClient) -> None:
    empty = await register_tenant(client, "empty@acme.example", "FOOD")
    response = await client.post(f"{REC}/by-text", json={"query": "x"}, headers=empty.headers)
    assert response.status_code == 200
    assert response.json()["results"] == []


# --- Logging, feedback, analytics ---


async def test_queries_are_logged(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = (
        await client.post(
            f"{REC}/by-item",
            json={"external_id": "job-1", "filters": {"location": "Delhi"}},
            headers=hr.headers,
        )
    ).json()

    async with session_factory() as session:
        entry = await session.get(RecommendationLog, uuid.UUID(body["query_id"]))
    assert entry is not None
    assert entry.query_type is QueryType.ITEM_ID
    assert entry.query_input == {"external_id": "job-1"}
    assert entry.filters_applied == {"location": "Delhi"}
    assert entry.results_count == 1
    assert entry.top_result_external_id == "job-3"
    assert entry.latency_ms == body["latency_ms"]


async def test_feedback(
    client: AsyncClient, hr: TenantAuth, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    query = (await client.post(f"{REC}/by-text", json={"query": "x"}, headers=hr.headers)).json()

    response = await client.post(
        f"{REC}/feedback",
        json={"query_id": query["query_id"], "external_item_id": "job-1", "feedback_type": "CLICK"},
        headers=hr.headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["query_id"] == query["query_id"]
    async with session_factory() as session:
        stored = (await session.scalars(select(UserFeedback))).one()
    assert (stored.external_item_id, stored.feedback_type) == ("job-1", "CLICK")


async def test_feedback_for_unknown_or_foreign_query_is_404(
    client: AsyncClient, hr: TenantAuth
) -> None:
    other = await register_tenant(client, "other@acme.example")
    query = (await client.post(f"{REC}/by-text", json={"query": "x"}, headers=hr.headers)).json()
    payload = {"external_item_id": "job-1", "feedback_type": "APPLY"}

    unknown = await client.post(
        f"{REC}/feedback", json={**payload, "query_id": str(uuid.uuid4())}, headers=hr.headers
    )
    foreign = await client.post(
        f"{REC}/feedback", json={**payload, "query_id": query["query_id"]}, headers=other.headers
    )
    bad_type = await client.post(
        f"{REC}/feedback",
        json={**payload, "query_id": query["query_id"], "feedback_type": "LOVE"},
        headers=hr.headers,
    )

    assert (unknown.status_code, foreign.status_code, bad_type.status_code) == (404, 404, 422)


async def test_analytics_overview(client: AsyncClient, hr: TenantAuth) -> None:
    for external_id in ("job-1", "job-1", "job-2"):
        await client.post(f"{REC}/by-item", json={"external_id": external_id}, headers=hr.headers)
    await client.post(f"{REC}/by-text", json={"query": "x"}, headers=hr.headers)

    response = await client.get("/api/v1/analytics/overview", headers=hr.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_items"] == 3
    assert body["total_recommendations_today"] == 4
    assert body["total_recommendations_this_month"] == 4
    assert isinstance(body["avg_latency_ms"], int)
    assert body["top_queried_items"] == [
        {"external_id": "job-1", "count": 2},
        {"external_id": "job-2", "count": 1},
    ]
    assert sum(i["count"] for i in body["top_recommended_items"]) == 4
    assert body["embedding_status_breakdown"] == {
        "PENDING": 0,
        "PROCESSING": 0,
        "DONE": 3,
        "FAILED": 0,
    }


async def test_analytics_for_new_tenant(client: AsyncClient) -> None:
    fresh = await register_tenant(client, "fresh@acme.example", "EDTECH")

    body = (await client.get("/api/v1/analytics/overview", headers=fresh.headers)).json()

    assert body["total_items"] == 0
    assert body["avg_latency_ms"] is None
    assert body["top_queried_items"] == []


async def test_feedback_summary(client: AsyncClient, hr: TenantAuth) -> None:
    query = (await client.post(f"{REC}/by-text", json={"query": "x"}, headers=hr.headers)).json()
    for feedback_type in ("CLICK", "CLICK", "THUMBS_DOWN"):
        await client.post(
            f"{REC}/feedback",
            json={
                "query_id": query["query_id"],
                "external_item_id": "job-1",
                "feedback_type": feedback_type,
            },
            headers=hr.headers,
        )

    body = (await client.get("/api/v1/analytics/feedback-summary", headers=hr.headers)).json()

    assert body["days"] == 30
    assert body["total"] == 3
    assert body["by_type"] == {
        "CLICK": 2,
        "THUMBS_UP": 0,
        "THUMBS_DOWN": 1,
        "PURCHASE": 0,
        "APPLY": 0,
        "IGNORE": 0,
    }


async def test_every_response_has_a_request_id(client: AsyncClient) -> None:
    generated = await client.get("/health")
    echoed = await client.get("/health", headers={"X-Request-ID": "gateway-123"})
    rejected = await client.get("/health", headers={"X-Request-ID": "bad id with spaces"})

    uuid.UUID(generated.headers["X-Request-ID"])
    assert echoed.headers["X-Request-ID"] == "gateway-123"
    assert rejected.headers["X-Request-ID"] != "bad id with spaces"
