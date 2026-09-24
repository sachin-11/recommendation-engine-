"""Orchestrates TextBuilder -> OpenAIEmbedder -> PineconeService and tracks item/batch status.

Work is claimed by atomically flipping items PENDING -> PROCESSING, so API background
tasks and any number of workers can run side by side without embedding an item twice.
If OpenAI or Pinecone is unreachable, claimed items go back to PENDING and are retried
later by the worker; only item-specific problems mark an item FAILED.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_object_session, async_sessionmaker

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.models.base import utcnow
from app.models.item import EmbeddingStatus, Item
from app.models.item_batch import BatchStatus, ItemBatch
from app.models.tenant import Tenant
from app.services.embedding.openai_embedder import (
    EmbeddingInputError,
    EmbeddingUnavailableError,
    OpenAIEmbedder,
)
from app.services.embedding.pinecone_service import PineconeService, VectorStoreUnavailableError
from app.services.embedding.text_builder import TextBuilder

logger = logging.getLogger(__name__)

# Raised when a dependency is down; the affected items are back in PENDING.
UpstreamUnavailableError = (EmbeddingUnavailableError, VectorStoreUnavailableError)


@dataclass
class ItemResult:
    external_id: str
    status: EmbeddingStatus
    error: str | None = None


@dataclass
class BatchResult:
    batch_id: uuid.UUID
    status: BatchStatus
    total_items: int
    processed_items: int
    failed_items: int
    error: str | None = None
    results: list[ItemResult] = field(default_factory=list)


def build_metadata(item_data: dict[str, Any], tenant: Tenant, external_id: str) -> dict[str, Any]:
    """Filterable fields stored with the vector: external_id, tenant_id and filter_fields."""
    metadata: dict[str, Any] = {"external_id": external_id, "tenant_id": str(tenant.id)}
    for name in tenant.domain_config.get("filter_fields") or []:
        if item_data.get(name) not in (None, "", []):
            metadata[name] = item_data[name]
    return metadata


class EmbeddingPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: OpenAIEmbedder,
        vector_store: PineconeService,
        text_builder: TextBuilder | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._vector_store = vector_store
        self._text_builder = text_builder or TextBuilder()

    # --- Public API ---

    async def process_item(self, item: Item, tenant: Tenant) -> bool:
        """Embed one item that is attached to a session. True when it ended up DONE."""
        session = async_object_session(item)
        if session is None:
            raise ValueError("process_item needs an item attached to a session")
        item.embedding_status = EmbeddingStatus.PROCESSING
        [result] = await self.process_items(session, [item], tenant)
        return result.status is EmbeddingStatus.DONE

    async def process_items(
        self, session: AsyncSession, items: list[Item], tenant: Tenant
    ) -> list[ItemResult]:
        """Embed and upsert items that are already claimed (PROCESSING), then commit.

        Raises EmbeddingUnavailableError / VectorStoreUnavailableError after returning
        the items to PENDING.
        """
        if not items:
            return []
        try:
            await self._embed_and_store(items, tenant)
        except UpstreamUnavailableError:
            for item in items:
                if item.embedding_status is EmbeddingStatus.PROCESSING:
                    item.embedding_status = EmbeddingStatus.PENDING
            await session.commit()
            raise
        await session.commit()
        return [
            ItemResult(item.external_id, item.embedding_status, item.item_metadata.get("error"))
            for item in items
        ]

    async def process_batch(self, batch_id: uuid.UUID) -> BatchResult:
        """Process every PENDING item of a batch, in chunks, and keep its counters current."""
        async with self._session_factory() as session:
            batch = await session.get(ItemBatch, batch_id)
            if batch is None:
                raise NotFoundError(f"Batch '{batch_id}' not found")
            tenant = await session.get(Tenant, batch.tenant_id)
            if tenant is None:
                raise NotFoundError(f"Tenant '{batch.tenant_id}' not found")

            error: str | None = None
            try:
                while items := await claim_items(
                    session, Item.batch_id == batch_id, limit=settings.WORKER_CHUNK_SIZE
                ):
                    await self.process_items(session, items, tenant)
                    await refresh_batch(session, batch)
            except UpstreamUnavailableError as exc:
                error = str(exc)
                logger.warning("Batch %s paused, items stay PENDING for retry: %s", batch_id, exc)

            await refresh_batch(session, batch)
            return BatchResult(
                batch_id=batch.id,
                status=batch.status,
                total_items=batch.total_items,
                processed_items=batch.processed_items,
                failed_items=batch.failed_items,
                error=error,
            )

    async def process_pending(self, limit: int | None = None) -> int:
        """Worker entry point: recover stale work, then process one chunk of any tenant's
        PENDING items. Returns how many items were claimed."""
        async with self._session_factory() as session:
            await release_stale_items(session)
            items = await claim_items(session, limit=limit or settings.WORKER_CHUNK_SIZE)
            by_tenant: dict[uuid.UUID, list[Item]] = defaultdict(list)
            for item in items:
                by_tenant[item.tenant_id].append(item)

            try:
                for tenant_id, tenant_items in by_tenant.items():
                    tenant = await session.get(Tenant, tenant_id)
                    if tenant is None:  # deleted mid-flight; its items cascade away
                        continue
                    await self.process_items(session, tenant_items, tenant)
            except UpstreamUnavailableError:
                # Items of tenants not reached yet are still PROCESSING; hand them back too.
                await release_items(session, [i.id for i in items])
                raise
            finally:
                for batch_id in {i.batch_id for i in items if i.batch_id}:
                    if batch := await session.get(ItemBatch, batch_id):
                        await refresh_batch(session, batch)
            return len(items)

    # --- Internals ---

    async def _embed_and_store(self, items: list[Item], tenant: Tenant) -> None:
        texts: dict[uuid.UUID, str] = {}
        for item in items:
            text = self._text_builder.build_embedding_text(item.raw_data, tenant.domain_config)
            if text:
                texts[item.id] = text
            else:
                fields = tenant.domain_config.get("searchable_fields") or []
                self._fail(item, tenant, f"No embeddable text: none of {fields} has a value")

        embeddable = [item for item in items if item.id in texts]
        if not embeddable:
            return
        vectors = await self._embed(embeddable, texts, tenant)

        ready = [item for item in embeddable if item.id in vectors]
        if not ready:
            return
        await self._vector_store.ensure_index_exists(tenant.id, self._embedder.dimension)
        metadata = {
            item.id: build_metadata(item.raw_data, tenant, item.external_id) for item in ready
        }
        outcome = await self._vector_store.upsert_batch(
            tenant.id,
            [
                {"id": str(item.id), "values": vectors[item.id], "metadata": metadata[item.id]}
                for item in ready
            ],
        )
        failed_ids = set(outcome["failed_ids"])
        for item in ready:
            if str(item.id) in failed_ids:
                self._fail(item, tenant, "Pinecone upsert failed")
            else:
                item.embedding_status = EmbeddingStatus.DONE
                item.pinecone_id = str(item.id)
                item.item_metadata = metadata[item.id]

    async def _embed(
        self, items: list[Item], texts: dict[uuid.UUID, str], tenant: Tenant
    ) -> dict[uuid.UUID, list[float]]:
        try:
            embedded = await self._embedder.embed_batch([texts[item.id] for item in items])
            return {item.id: vector for item, vector in zip(items, embedded, strict=True)}
        except EmbeddingInputError as exc:
            if len(items) == 1:
                self._fail(items[0], tenant, f"OpenAI rejected the input text: {exc}")
                return {}

        # One bad input rejects the whole request; retry one by one to isolate it.
        vectors: dict[uuid.UUID, list[float]] = {}
        for item in items:
            try:
                vectors[item.id] = await self._embedder.embed_text(texts[item.id])
            except EmbeddingInputError as exc:
                self._fail(item, tenant, f"OpenAI rejected the input text: {exc}")
        return vectors

    @staticmethod
    def _fail(item: Item, tenant: Tenant, error: str) -> None:
        logger.warning("Item %s (%s) failed: %s", item.id, item.external_id, error)
        item.embedding_status = EmbeddingStatus.FAILED
        item.item_metadata = {
            **build_metadata(item.raw_data, tenant, item.external_id),
            "error": error,
        }


# --- Claiming and batch bookkeeping (shared with the API and the worker) ---


async def claim_items(
    session: AsyncSession, *conditions: ColumnElement[bool], limit: int
) -> list[Item]:
    """Atomically move up to `limit` PENDING items to PROCESSING and return them."""
    candidates = (
        await session.scalars(
            select(Item.id)
            .where(Item.embedding_status == EmbeddingStatus.PENDING, *conditions)
            .order_by(Item.created_at, Item.id)
            .limit(limit)
        )
    ).all()
    if not candidates:
        return []
    # The status check in the WHERE clause makes this safe against concurrent claimers.
    claimed = (
        await session.scalars(
            update(Item)
            .where(Item.id.in_(candidates), Item.embedding_status == EmbeddingStatus.PENDING)
            .values(embedding_status=EmbeddingStatus.PROCESSING, updated_at=utcnow())
            .returning(Item.id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    await session.commit()
    if not claimed:
        return []
    result = await session.scalars(
        select(Item).where(Item.id.in_(claimed)).execution_options(populate_existing=True)
    )
    return list(result.all())


async def release_items(session: AsyncSession, item_ids: list[uuid.UUID]) -> None:
    """Hand PROCESSING items back to the queue."""
    if not item_ids:
        return
    await session.execute(
        update(Item)
        .where(Item.id.in_(item_ids), Item.embedding_status == EmbeddingStatus.PROCESSING)
        .values(embedding_status=EmbeddingStatus.PENDING, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def release_stale_items(session: AsyncSession) -> int:
    """Return items stuck in PROCESSING (their worker died) to PENDING."""
    cutoff = utcnow() - timedelta(seconds=settings.WORKER_STALE_AFTER_SECONDS)
    result = await session.execute(
        update(Item)
        .where(Item.embedding_status == EmbeddingStatus.PROCESSING, Item.updated_at < cutoff)
        .values(embedding_status=EmbeddingStatus.PENDING, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    released = result.rowcount or 0  # type: ignore[attr-defined]
    if released:
        logger.warning("Released %d stale PROCESSING items back to PENDING", released)
    return released


async def refresh_batch(session: AsyncSession, batch: ItemBatch) -> ItemBatch:
    """Recount a batch's items and mark it complete once none are outstanding."""
    if batch.is_complete:
        return batch
    rows = await session.execute(
        select(Item.embedding_status, func.count())
        .where(Item.batch_id == batch.id)
        .group_by(Item.embedding_status)
    )
    counts: dict[EmbeddingStatus, int] = {status: count for status, count in rows.tuples()}
    done = counts.get(EmbeddingStatus.DONE, 0)
    failed = counts.get(EmbeddingStatus.FAILED, 0)
    processing = counts.get(EmbeddingStatus.PROCESSING, 0)
    outstanding = counts.get(EmbeddingStatus.PENDING, 0) + processing

    batch.processed_items = done
    batch.failed_items = failed
    if outstanding == 0:
        batch.status = BatchStatus.PARTIAL_FAIL if failed else BatchStatus.DONE
        batch.completed_at = utcnow()
    elif done or failed or processing:
        batch.status = BatchStatus.PROCESSING
    await session.commit()
    return batch
