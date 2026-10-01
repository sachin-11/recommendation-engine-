import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import EmbeddingStatus, Item
from app.services.embedding import openai_embedder
from app.services.embedding.text_builder import TextBuilder
from app.services.recommendation.filter_builder import FilterBuilder, matches_filter
from app.services.recommendation.keyword_search import KeywordSearch
from tests.conftest import TenantAuth, register_tenant
from tests.test_recommend_endpoints import HR_CONFIG, JOBS


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


async def _upload(client: AsyncClient, auth: TenantAuth, items: list[dict[str, Any]]) -> None:
    response = await client.post(
        "/api/v1/items/upload", json={"items": items, "async": False}, headers=auth.headers
    )
    assert response.json()["succeeded"] == len(items), response.text


@pytest.fixture
async def hr(client: AsyncClient) -> TenantAuth:
    auth = await register_tenant(client, "jobs@acme.example", "HR", HR_CONFIG)
    await _upload(client, auth, JOBS)
    return auth


@pytest.fixture
def search(session_factory: async_sessionmaker[AsyncSession], hr: TenantAuth) -> Any:
    async def run(text: str, filters: dict[str, Any] | None = None, k: int = 10) -> list[str]:
        pinecone_filter = FilterBuilder().build_pinecone_filter(filters, HR_CONFIG)
        async with session_factory() as session:
            matches = await KeywordSearch(session).search(
                uuid.UUID(hr.tenant_id), text, pinecone_filter, k
            )
        return [m.external_id for m in matches]

    return run


async def test_exact_terms_match(search: Any) -> None:
    assert await search("FastAPI") == ["job-1"]
    assert await search("fastapi") == ["job-1"]  # case does not matter
    assert await search("spark") == ["job-3"]


async def test_any_word_matches_and_more_matches_rank_higher(search: Any) -> None:
    assert await search("python fastapi react") == ["job-1", "job-2"]
    assert set(await search("react spark")) == {"job-2", "job-3"}


async def test_filters_apply_like_vector_search(search: Any) -> None:
    assert set(await search("engineer")) == {"job-1", "job-2", "job-3"}
    assert set(await search("engineer", {"location": "Delhi"})) == {"job-1", "job-3"}
    assert await search("engineer", {"experience_years": {"gte": 6}}) == ["job-3"]
    assert len(await search("engineer", k=2)) == 2


async def test_field_names_and_empty_queries_match_nothing(search: Any) -> None:
    assert await search("description") == []
    assert await search("   ") == []
    assert await search("?!") == []
    assert await search("kubernetes") == []


async def test_only_this_workspaces_embedded_items(
    client: AsyncClient,
    hr: TenantAuth,
    search: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    other = await register_tenant(client, "other@acme.example", "HR", HR_CONFIG)
    await _upload(client, other, [{"external_id": "x-1", "description": "Kubernetes"}])
    async with session_factory() as session:
        await session.execute(
            update(Item)
            .where(Item.external_id == "job-2")
            .values(embedding_status=EmbeddingStatus.PENDING)
        )
        await session.commit()

    assert await search("kubernetes") == []
    assert await search("react") == []  # job-2 is being re-embedded

    await client.delete("/api/v1/items/job-1", headers=hr.headers)
    assert await search("fastapi") == []


def test_keyword_text_is_values_only() -> None:
    text = TextBuilder().build_keyword_text(JOBS[0], HR_CONFIG)
    assert text == "Backend Engineer Python FastAPI services Python FastAPI"
    assert "title" not in text


@pytest.mark.parametrize(
    ("metadata", "pinecone_filter", "expected"),
    [
        ({"city": "Delhi"}, {"city": {"$eq": "Delhi"}}, True),
        ({"city": "Pune"}, {"city": {"$in": ["Delhi", "Pune"]}}, True),
        ({"tags": ["a", "b"]}, {"tags": {"$eq": "b"}}, True),  # any element of a list
        ({"tags": ["a", "b"]}, {"tags": {"$nin": ["b"]}}, False),
        ({"years": 5}, {"years": {"$gte": 3, "$lt": 5}}, False),
        ({"years": "5"}, {"years": {"$gte": 3}}, False),  # ranges need numbers
        ({}, {"city": {"$ne": "Delhi"}}, True),
        ({}, {"city": {"$eq": "Delhi"}}, False),
    ],
)
def test_matches_filter(
    metadata: dict[str, Any], pinecone_filter: dict[str, Any], expected: bool
) -> None:
    assert matches_filter(metadata, pinecone_filter) is expected
