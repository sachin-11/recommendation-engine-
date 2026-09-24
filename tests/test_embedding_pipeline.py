"""Pipeline tests with OpenAI and Pinecone replaced by in-memory fakes (see tests/fakes.py)."""

import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BatchStatus, EmbeddingStatus, Item, ItemBatch, Tenant
from app.models.base import utcnow
from app.services.embedding import openai_embedder
from app.services.embedding.openai_embedder import (
    EmbeddingUnavailableError,
    OpenAIEmbedder,
    truncate_to_tokens,
)
from app.services.embedding.pinecone_service import PineconeService, sanitize_metadata
from app.services.embedding.pipeline import EmbeddingPipeline, claim_items, release_stale_items
from app.services.item_service import ItemService
from tests.conftest import HR_CONFIG
from tests.fakes import FakeOpenAIClient, FakeVectorStore, fake_vector

JOBS: list[dict[str, Any]] = [
    {
        "external_id": "job-1",
        "title": "Backend Engineer",
        "description": "Build Python APIs with FastAPI",
        "skills": ["Python", "FastAPI"],
        "location": "Bangalore",
        "employment_type": "full_time",
    },
    {
        "external_id": "job-2",
        "title": "Data Scientist",
        "description": "Train recommendation models",
        "location": "Remote",
    },
    {
        "external_id": "job-3",
        "title": "ML Engineer",
        "description": "Ship embeddings to production",
    },
]


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    # No exponential backoff sleeps in tests.
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def tenant(session_factory: async_sessionmaker[AsyncSession]) -> Tenant:
    async with session_factory() as session:
        tenant = Tenant(
            name="Acme", email="acme@example.com", domain_type="HR", domain_config=HR_CONFIG
        )
        session.add(tenant)
        await session.commit()
        return tenant


async def queue_batch(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, items: list[dict[str, Any]]
) -> uuid.UUID:
    async with session_factory() as session:
        batch = await ItemService(session, tenant).ingest_async(items)
        return batch.id


async def load_items(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Item]:
    async with session_factory() as session:
        items = (await session.scalars(select(Item))).all()
        return {item.external_id: item for item in items}


# --- Full flow ---


async def test_process_batch_embeds_and_stores_every_item(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    vector_store: FakeVectorStore,
    openai_client: FakeOpenAIClient,
) -> None:
    batch_id = await queue_batch(session_factory, tenant, JOBS)

    result = await pipeline.process_batch(batch_id)

    assert result.status is BatchStatus.DONE
    assert (result.total_items, result.processed_items, result.failed_items) == (3, 3, 0)
    assert result.error is None

    items = await load_items(session_factory)
    assert {i.embedding_status for i in items.values()} == {EmbeddingStatus.DONE}

    vectors = vector_store.vectors(tenant.id)
    job1 = items["job-1"]
    assert job1.pinecone_id == str(job1.id)
    stored = vectors[job1.pinecone_id]
    text = (
        "description: Build Python APIs with FastAPI title: Backend Engineer skills: Python FastAPI"
    )
    assert stored["values"] == fake_vector(text)
    assert stored["metadata"] == {
        "external_id": "job-1",
        "tenant_id": str(tenant.id),
        "location": "Bangalore",
        "employment_type": "full_time",
    }
    assert job1.item_metadata == stored["metadata"]
    # All three texts went to OpenAI in one request.
    assert len(openai_client.embeddings.calls) == 1

    async with session_factory() as session:
        batch = await session.get(ItemBatch, batch_id)
        assert batch is not None and batch.completed_at is not None


async def test_identical_text_is_served_from_redis_cache(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    openai_client: FakeOpenAIClient,
) -> None:
    await pipeline.process_batch(await queue_batch(session_factory, tenant, JOBS[:1]))
    # Same content under a new id: the text is identical, so no new OpenAI call.
    await pipeline.process_batch(
        await queue_batch(session_factory, tenant, [{**JOBS[0], "external_id": "job-copy"}])
    )

    assert len(openai_client.embeddings.calls) == 1
    items = await load_items(session_factory)
    assert items["job-copy"].embedding_status is EmbeddingStatus.DONE


async def test_process_item_single(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    vector_store: FakeVectorStore,
) -> None:
    async with session_factory() as session:
        [item] = await ItemService(session, tenant).upsert_items(
            JOBS[:1], status=EmbeddingStatus.PENDING
        )
        assert await pipeline.process_item(item, tenant) is True
        assert item.embedding_status is EmbeddingStatus.DONE
    assert str(item.id) in vector_store.vectors(tenant.id)


# --- Failures ---


async def test_item_without_embeddable_text_fails_with_error(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
) -> None:
    batch_id = await queue_batch(
        session_factory, tenant, [JOBS[0], {"external_id": "empty", "location": "Pune"}]
    )

    result = await pipeline.process_batch(batch_id)

    assert result.status is BatchStatus.PARTIAL_FAIL
    assert (result.processed_items, result.failed_items) == (1, 1)
    empty = (await load_items(session_factory))["empty"]
    assert empty.embedding_status is EmbeddingStatus.FAILED
    assert "No embeddable text" in empty.item_metadata["error"]


async def test_pinecone_upsert_failure_marks_item_failed(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    vector_store: FakeVectorStore,
) -> None:
    batch_id = await queue_batch(session_factory, tenant, JOBS)
    failing = (await load_items(session_factory))["job-2"]
    vector_store.fail_ids = {str(failing.id)}

    result = await pipeline.process_batch(batch_id)

    assert result.status is BatchStatus.PARTIAL_FAIL
    assert (result.processed_items, result.failed_items) == (2, 1)
    job2 = (await load_items(session_factory))["job-2"]
    assert job2.embedding_status is EmbeddingStatus.FAILED
    assert job2.item_metadata["error"] == "Pinecone upsert failed"
    assert job2.pinecone_id is None


async def test_rejected_input_fails_only_that_item(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    openai_client: FakeOpenAIClient,
) -> None:
    openai_client.embeddings.reject_marker = "Data Scientist"
    batch_id = await queue_batch(session_factory, tenant, JOBS)

    result = await pipeline.process_batch(batch_id)

    assert (result.processed_items, result.failed_items) == (2, 1)
    job2 = (await load_items(session_factory))["job-2"]
    assert job2.embedding_status is EmbeddingStatus.FAILED
    assert "OpenAI rejected the input" in job2.item_metadata["error"]


async def test_openai_down_leaves_items_pending_then_worker_recovers(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    openai_client: FakeOpenAIClient,
) -> None:
    batch_id = await queue_batch(session_factory, tenant, JOBS)
    openai_client.embeddings.down = True

    result = await pipeline.process_batch(batch_id)

    assert result.status is BatchStatus.PENDING
    assert result.error is not None and "OpenAI unavailable" in result.error
    items = await load_items(session_factory)
    assert {i.embedding_status for i in items.values()} == {EmbeddingStatus.PENDING}

    # OpenAI recovers; the worker picks the items up.
    openai_client.embeddings.down = False
    assert await pipeline.process_pending() == 3
    items = await load_items(session_factory)
    assert {i.embedding_status for i in items.values()} == {EmbeddingStatus.DONE}
    async with session_factory() as session:
        batch = await session.get(ItemBatch, batch_id)
        assert batch is not None and batch.status is BatchStatus.DONE


async def test_pinecone_down_leaves_items_pending(
    pipeline: EmbeddingPipeline,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    vector_store: FakeVectorStore,
) -> None:
    vector_store.unavailable = True
    batch_id = await queue_batch(session_factory, tenant, JOBS)

    result = await pipeline.process_batch(batch_id)

    assert result.error is not None
    items = await load_items(session_factory)
    assert {i.embedding_status for i in items.values()} == {EmbeddingStatus.PENDING}


async def test_worker_with_nothing_to_do(pipeline: EmbeddingPipeline) -> None:
    assert await pipeline.process_pending() == 0


# --- Claiming ---


async def test_claimed_items_are_not_claimed_again(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant
) -> None:
    await queue_batch(session_factory, tenant, JOBS)
    async with session_factory() as first, session_factory() as second:
        claimed_a = await claim_items(first, limit=2)
        claimed_b = await claim_items(second, limit=10)

    assert len(claimed_a) == 2 and len(claimed_b) == 1
    assert {i.id for i in claimed_a}.isdisjoint({i.id for i in claimed_b})


async def test_stale_processing_items_are_released(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant
) -> None:
    await queue_batch(session_factory, tenant, JOBS[:1])
    async with session_factory() as session:
        await claim_items(session, limit=10)
        await session.execute(update(Item).values(updated_at=utcnow() - timedelta(hours=1)))
        await session.commit()

        assert await release_stale_items(session) == 1

    items = await load_items(session_factory)
    assert items["job-1"].embedding_status is EmbeddingStatus.PENDING


# --- Embedder and Pinecone details ---


async def test_embedder_without_api_key_is_unavailable() -> None:
    with pytest.raises(EmbeddingUnavailableError):
        await OpenAIEmbedder(None).embed_text("hello")


async def test_embedder_deduplicates_and_keeps_order(openai_client: FakeOpenAIClient) -> None:
    embedder = OpenAIEmbedder(openai_client, None, dimension=8)  # type: ignore[arg-type]

    vectors = await embedder.embed_batch(["a", "b", "a"])

    assert vectors == [fake_vector("a"), fake_vector("b"), fake_vector("a")]
    assert openai_client.embeddings.calls == [["a", "b"]]


def test_long_text_is_truncated_to_token_limit() -> None:
    text = "word " * 20_000
    truncated = truncate_to_tokens(text, 100)
    assert len(truncated) < len(text)
    assert truncate_to_tokens("short text", 100) == "short text"


def test_metadata_is_sanitized_for_pinecone() -> None:
    assert sanitize_metadata(
        {"a": "x", "b": 2, "c": None, "d": ["x", None, 3], "e": {"k": 1}, "f": True}
    ) == {"a": "x", "b": 2, "d": ["x", "3"], "e": '{"k": 1}', "f": True}


async def test_pinecone_service_reports_failed_chunks() -> None:
    index = MagicMock()
    index.upsert.side_effect = RuntimeError("boom")
    client = MagicMock()
    client.has_index.return_value = True
    client.Index.return_value = index
    service = PineconeService(client)
    tenant_id = uuid.uuid4()

    await service.ensure_index_exists(tenant_id)
    result = await service.upsert_batch(tenant_id, [{"id": "v1", "values": [0.1], "metadata": {}}])

    assert result == {"upserted": 0, "failed": 1, "failed_ids": ["v1"]}
    client.create_index.assert_not_called()
    client.Index.assert_called_once_with(name="reco-shared")
    assert index.upsert.call_args.kwargs["namespace"] == str(tenant_id)


async def test_pinecone_service_creates_missing_index() -> None:
    client = MagicMock()
    client.has_index.return_value = False
    service = PineconeService(client, cloud="aws", region="us-east-1")

    name = await service.ensure_index_exists("12345678-aaaa", dimension=1536)
    await service.ensure_index_exists("87654321-bbbb", dimension=1536)

    # One shared index for every tenant, created once.
    assert name == "reco-shared"
    client.create_index.assert_called_once()
    kwargs = client.create_index.call_args.kwargs
    assert (kwargs["name"], kwargs["dimension"], kwargs["metric"]) == (
        "reco-shared",
        1536,
        "cosine",
    )


async def test_pinecone_stats_only_show_the_tenants_own_namespace() -> None:
    mine, other = uuid.uuid4(), uuid.uuid4()
    stats = MagicMock(dimension=1536, index_fullness=0.0)
    stats.namespaces = {str(mine): MagicMock(vector_count=3), str(other): MagicMock(vector_count=9)}
    index = MagicMock()
    index.describe_index_stats.return_value = stats
    client = MagicMock()
    client.Index.return_value = index

    result = await PineconeService(client).get_index_stats(mine)

    assert result["total_vector_count"] == 3
    assert result["namespaces"] == {str(mine): 3}
    assert str(other) not in str(result)


async def test_pinecone_writes_and_deletes_are_scoped_to_the_namespace() -> None:
    tenant_id = uuid.uuid4()
    index = MagicMock()
    client = MagicMock()
    client.Index.return_value = index
    service = PineconeService(client)

    assert await service.delete_items(tenant_id, ["v1", "v2"]) is True
    await service.delete_tenant_vectors(tenant_id)

    assert index.delete.call_args_list[0].kwargs == {
        "ids": ["v1", "v2"],
        "namespace": str(tenant_id),
    }
    assert index.delete.call_args_list[1].kwargs == {
        "delete_all": True,
        "namespace": str(tenant_id),
    }
    client.delete_index.assert_not_called()


async def test_deleting_a_missing_namespace_is_fine() -> None:
    from pinecone import NotFoundError as PineconeNotFoundError

    index = MagicMock()
    index.delete.side_effect = PineconeNotFoundError("Namespace not found", 404)
    client = MagicMock()
    client.Index.return_value = index
    service = PineconeService(client)

    await service.delete_tenant_vectors(uuid.uuid4())
    assert await service.delete_item(uuid.uuid4(), "v1") is True


async def test_legacy_index_listing_never_includes_the_shared_index() -> None:
    client = MagicMock()
    client.list_indexes.return_value = [
        MagicMock(name=n) for n in ("reco-shared", "reco-736da681", "reco-0098c77d", "other")
    ]
    for mock, name in zip(
        client.list_indexes.return_value,
        ("reco-shared", "reco-736da681", "reco-0098c77d", "other"),
        strict=True,
    ):
        mock.name = name
    service = PineconeService(client)

    assert await service.list_legacy_indexes() == ["reco-0098c77d", "reco-736da681"]
    with pytest.raises(ValueError):
        await service.delete_legacy_index("reco-shared")
