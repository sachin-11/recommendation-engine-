"""QueryEngine against the in-memory Pinecone fake, plus PineconeService.query against a
mocked Pinecone client."""

import time
import uuid
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import pytest
from pinecone import NotFoundError as PineconeNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models import EmbeddingStatus, QueryType, Tenant
from app.services.embedding import openai_embedder
from app.services.embedding.openai_embedder import OpenAIEmbedder
from app.services.embedding.pinecone_service import (
    PineconeService,
    VectorStoreQueryError,
    VectorStoreTimeoutError,
)
from app.services.embedding.pipeline import EmbeddingPipeline
from app.services.item_service import ItemService
from app.services.recommendation.cache import RecommendationCache
from app.services.recommendation.query_engine import BatchQuery, QueryEngine
from tests.fakes import FakeOpenAIClient, FakeVectorStore, fake_vector

CONFIG: dict[str, Any] = {
    "primary_embedding_field": "description",
    "searchable_fields": ["title", "description", "skills"],
    "filter_fields": ["location", "experience_years"],
    "item_label": "job",
}
JOBS: list[dict[str, Any]] = [
    {
        "external_id": "job-1",
        "title": "Backend Engineer",
        "description": "Python APIs",
        "location": "Delhi",
        "experience_years": 5,
    },
    {
        "external_id": "job-2",
        "title": "Data Scientist",
        "description": "Ranking models",
        "location": "Remote",
        "experience_years": 2,
    },
    {
        "external_id": "job-3",
        "title": "ML Engineer",
        "description": "Ship embeddings",
        "location": "Delhi",
        "experience_years": 8,
    },
]
# The exact text job-1 was embedded from, so a text query for it scores 1.0.
JOB1_TEXT = "description: Python APIs title: Backend Engineer"


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def tenant(
    session_factory: async_sessionmaker[AsyncSession], pipeline: EmbeddingPipeline
) -> Tenant:
    async with session_factory() as session:
        tenant = Tenant(
            name="Acme", email="acme@example.com", domain_type="HR", domain_config=CONFIG
        )
        session.add(tenant)
        await session.commit()
        stored = await ItemService(session, tenant).upsert_items(
            JOBS, status=EmbeddingStatus.PROCESSING
        )
        await pipeline.process_items(session, stored, tenant)
    return tenant


@pytest.fixture
async def engine(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: OpenAIEmbedder,
    vector_store: FakeVectorStore,
    redis: fakeredis.FakeAsyncRedis,
) -> Any:
    async with session_factory() as session:
        yield QueryEngine(session, embedder, vector_store, RecommendationCache(redis))  # type: ignore[arg-type]


# --- By text ---


async def test_by_text_ranks_the_closest_item_first(engine: QueryEngine, tenant: Tenant) -> None:
    rec = await engine.recommend_by_text(JOB1_TEXT, tenant, top_k=3)

    assert rec.query_type is QueryType.TEXT
    assert rec.query_input == {"query": JOB1_TEXT}
    assert rec.cache_status == "MISS"
    assert [r["rank"] for r in rec.results] == [1, 2, 3]
    top = rec.results[0]
    assert top["external_id"] == "job-1"
    assert top["score"] == pytest.approx(1.0)
    assert top["score_label"] == "Excellent Match"
    assert top["metadata"] == {"location": "Delhi", "experience_years": 5}
    scores = [r["score"] for r in rec.results]
    assert scores == sorted(scores, reverse=True)


async def test_by_text_applies_filters(
    engine: QueryEngine, tenant: Tenant, vector_store: FakeVectorStore
) -> None:
    rec = await engine.recommend_by_text(
        JOB1_TEXT, tenant, filters={"location": "Delhi", "experience_years": {"gte": 6}}
    )

    assert [r["external_id"] for r in rec.results] == ["job-3"]
    assert vector_store.queries[-1]["filter"] == {
        "location": {"$eq": "Delhi"},
        "experience_years": {"$gte": 6},
    }


async def test_by_text_respects_top_k(engine: QueryEngine, tenant: Tenant) -> None:
    rec = await engine.recommend_by_text("anything", tenant, top_k=2)
    assert len(rec.results) == 2


async def test_raw_data_is_attached_and_not_cached(engine: QueryEngine, tenant: Tenant) -> None:
    rec = await engine.recommend_by_text(JOB1_TEXT, tenant, top_k=1, include_raw_data=True)

    assert rec.cache_status == "BYPASS"
    assert rec.results[0]["raw_data"]["title"] == "Backend Engineer"


async def test_invalid_filter_is_rejected_before_any_search(
    engine: QueryEngine, tenant: Tenant, vector_store: FakeVectorStore
) -> None:
    with pytest.raises(BadRequestError):
        await engine.recommend_by_text("x", tenant, filters={"salary": 1})
    assert vector_store.queries == []


# --- By item ---


async def test_by_item_excludes_the_item_itself(
    engine: QueryEngine, tenant: Tenant, vector_store: FakeVectorStore
) -> None:
    rec = await engine.recommend_by_item_id("job-1", tenant, top_k=2)

    ids = [r["external_id"] for r in rec.results]
    assert "job-1" not in ids
    assert len(ids) == 2
    # Queried by the stored vector's id, asking for one extra to make room for itself.
    last = vector_store.queries[-1]
    assert last["id"] is not None and last["vector"] is None
    assert last["top_k"] == 3


async def test_by_item_unknown_item_is_404(engine: QueryEngine, tenant: Tenant) -> None:
    with pytest.raises(NotFoundError):
        await engine.recommend_by_item_id("nope", tenant)


async def test_by_item_not_yet_embedded_is_409(
    engine: QueryEngine, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        await ItemService(session, tenant).upsert_items(
            [{"external_id": "new", "description": "fresh"}], status=EmbeddingStatus.PENDING
        )

    with pytest.raises(ConflictError, match="not embedded yet"):
        await engine.recommend_by_item_id("new", tenant)


# --- By profile ---


async def test_by_profile_uses_every_profile_field(
    engine: QueryEngine, tenant: Tenant, vector_store: FakeVectorStore
) -> None:
    profile = {
        "experience": "5 years backend",
        "skills": "Python, FastAPI",
        "preferred_location": "Remote",
    }

    rec = await engine.recommend_by_profile(profile, tenant, top_k=3)

    assert rec.query_type is QueryType.PROFILE
    assert len(rec.results) == 3
    # `skills` is a searchable item field, so it leads; other fields follow in order.
    expected = "skills: Python, FastAPI experience: 5 years backend preferred_location: Remote"
    assert vector_store.queries[-1]["vector"] == fake_vector(expected)


async def test_empty_profile_is_rejected(engine: QueryEngine, tenant: Tenant) -> None:
    with pytest.raises(BadRequestError):
        await engine.recommend_by_profile({"skills": "  "}, tenant)


# --- Batch ---


async def test_batch_embeds_all_texts_in_one_call(
    engine: QueryEngine, tenant: Tenant, openai_client: FakeOpenAIClient
) -> None:
    calls_before = len(openai_client.embeddings.calls)

    recs = await engine.recommend_batch(
        [
            BatchQuery("q1", "python developer"),
            BatchQuery("q2", "data science", {"location": "Remote"}),
        ],
        tenant,
        top_k=2,
    )

    assert set(recs) == {"q1", "q2"}
    assert len(recs["q1"].results) == 2
    assert [r["external_id"] for r in recs["q2"].results] == ["job-2"]
    assert openai_client.embeddings.calls[calls_before:] == [["python developer", "data science"]]


async def test_batch_reports_which_query_has_bad_filters(
    engine: QueryEngine, tenant: Tenant
) -> None:
    with pytest.raises(BadRequestError, match="query 'q2'"):
        await engine.recommend_batch(
            [BatchQuery("q1", "a"), BatchQuery("q2", "b", {"salary": 1})], tenant
        )


# --- Failures ---


async def test_pinecone_timeout_is_503_not_partial_results(
    engine: QueryEngine, tenant: Tenant, vector_store: FakeVectorStore
) -> None:
    vector_store.query_times_out = True

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await engine.recommend_by_text("python", tenant)

    assert "timed out" in exc_info.value.message
    assert exc_info.value.headers == {"Retry-After": "5"}


async def test_openai_down_is_503(
    engine: QueryEngine, tenant: Tenant, openai_client: FakeOpenAIClient
) -> None:
    openai_client.embeddings.down = True
    with pytest.raises(ServiceUnavailableError, match="Embedding service"):
        await engine.recommend_by_text("never embedded before", tenant)


# --- PineconeService.query with a mocked Pinecone client ---


def _mock_client(index: MagicMock) -> MagicMock:
    client = MagicMock()
    client.Index.return_value = index
    return client


def _match(id: str, score: float, metadata: dict[str, Any] | None) -> MagicMock:
    match = MagicMock()
    match.id, match.score, match.metadata = id, score, metadata
    return match


async def test_pinecone_query_maps_matches() -> None:
    index = MagicMock()
    index.query.return_value.matches = [
        _match("v1", 0.9, {"external_id": "a"}),
        _match("v2", 0.5, None),
    ]
    service = PineconeService(_mock_client(index))

    matches = await service.query(uuid.uuid4(), top_k=2, vector=[0.1], filter={"x": {"$eq": 1}})

    assert matches == [
        {"id": "v1", "score": 0.9, "metadata": {"external_id": "a"}},
        {"id": "v2", "score": 0.5, "metadata": {}},
    ]
    kwargs = index.query.call_args.kwargs
    assert kwargs["filter"] == {"x": {"$eq": 1}}
    assert kwargs["include_metadata"] is True
    assert kwargs["timeout"] == 5.0


async def test_pinecone_query_without_index_returns_nothing() -> None:
    client = MagicMock()
    client.Index.side_effect = PineconeNotFoundError("no index", 404)
    assert await PineconeService(client).query(uuid.uuid4(), top_k=5, vector=[0.1]) == []


async def test_pinecone_query_times_out() -> None:
    index = MagicMock()
    index.query.side_effect = lambda **_: time.sleep(0.5)
    service = PineconeService(_mock_client(index), query_timeout=0.05)

    with pytest.raises(VectorStoreTimeoutError):
        await service.query(uuid.uuid4(), top_k=5, vector=[0.1])


async def test_pinecone_query_bad_request_is_a_query_error() -> None:
    index = MagicMock()
    error = Exception("bad filter")
    error.status_code = 400  # type: ignore[attr-defined]
    index.query.side_effect = error
    with pytest.raises(VectorStoreQueryError):
        await PineconeService(_mock_client(index)).query(uuid.uuid4(), top_k=5, vector=[0.1])
