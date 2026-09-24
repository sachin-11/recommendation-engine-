"""Item ingestion, listing, deletion and index rebuild for one authenticated tenant."""

import logging
import math
import uuid
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.middleware.auth import AuthDep
from app.models.base import utcnow
from app.models.item import EmbeddingStatus, Item
from app.models.item_batch import BatchStatus, ItemBatch
from app.models.tenant import Tenant
from app.services.embedding.pinecone_service import PineconeService, VectorStoreUnavailableError
from app.services.embedding.pipeline import (
    EmbeddingPipeline,
    ItemResult,
    UpstreamUnavailableError,
    build_metadata,
    refresh_batch,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
# Rows per INSERT statement; keeps bind parameters well under Postgres/SQLite limits.
UPSERT_CHUNK_SIZE = 500


class ItemService:
    def __init__(self, session: AsyncSession, tenant: Tenant) -> None:
        self._session = session
        self._tenant = tenant

    # --- Ingestion ---

    async def upsert_items(
        self,
        items: list[dict[str, Any]],
        *,
        status: EmbeddingStatus,
        batch_id: uuid.UUID | None = None,
    ) -> list[Item]:
        """Insert items, or update them in place when the external_id already exists.

        Re-uploading an item resets it to `status` so it is embedded again. Duplicate
        external_ids within one call collapse to the last occurrence.
        """
        unique = list({item["external_id"]: item for item in items}.values())
        now = utcnow()
        rows = [
            {
                "id": uuid.uuid4(),
                "tenant_id": self._tenant.id,
                "batch_id": batch_id,
                "external_id": item["external_id"],
                "raw_data": item,
                "metadata": build_metadata(item, self._tenant, item["external_id"]),
                "embedding_status": status,
                "created_at": now,
                "updated_at": now,
            }
            for item in unique
        ]
        insert = postgresql.insert if self._dialect == "postgresql" else sqlite.insert
        for start in range(0, len(rows), UPSERT_CHUNK_SIZE):
            # Core insert on the table: keys are column names (`metadata`, not `item_metadata`).
            stmt = insert(Item.__table__).values(rows[start : start + UPSERT_CHUNK_SIZE])
            excluded = stmt.excluded
            await self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["tenant_id", "external_id"],
                    set_={
                        "batch_id": excluded.batch_id,
                        "raw_data": excluded.raw_data,
                        "metadata": excluded["metadata"],
                        "embedding_status": excluded.embedding_status,
                        "updated_at": excluded.updated_at,
                    },
                )
            )
        await self._session.commit()

        external_ids = [row["external_id"] for row in rows]
        loaded: list[Item] = []
        for start in range(0, len(external_ids), UPSERT_CHUNK_SIZE):
            result = await self._session.scalars(
                select(Item)
                .where(
                    Item.tenant_id == self._tenant.id,
                    Item.external_id.in_(external_ids[start : start + UPSERT_CHUNK_SIZE]),
                )
                .execution_options(populate_existing=True)
            )
            loaded.extend(result.all())
        order = {external_id: i for i, external_id in enumerate(external_ids)}
        return sorted(loaded, key=lambda item: order[item.external_id])

    async def ingest_sync(
        self, items: list[dict[str, Any]], pipeline: EmbeddingPipeline
    ) -> list[ItemResult]:
        """Store items and embed them before returning. Items are stored as PROCESSING so
        the worker does not pick them up concurrently."""
        stored = await self.upsert_items(items, status=EmbeddingStatus.PROCESSING)
        try:
            return await pipeline.process_items(self._session, stored, self._tenant)
        except UpstreamUnavailableError as exc:
            logger.warning("Sync ingestion deferred, %d items left PENDING: %s", len(stored), exc)
            raise ServiceUnavailableError(
                "Embedding service is temporarily unavailable. Items were saved as PENDING "
                "and will be processed automatically once it recovers."
            ) from exc

    async def ingest_async(self, items: list[dict[str, Any]]) -> ItemBatch:
        unique_count = len({item["external_id"] for item in items})
        batch = await self.create_batch(unique_count)
        await self.upsert_items(items, status=EmbeddingStatus.PENDING, batch_id=batch.id)
        return batch

    async def create_batch(self, total_items: int) -> ItemBatch:
        batch = ItemBatch(tenant_id=self._tenant.id, total_items=total_items)
        if total_items == 0:
            batch.status = BatchStatus.DONE
            batch.completed_at = utcnow()
        self._session.add(batch)
        await self._session.commit()
        return batch

    async def rebuild(self) -> ItemBatch:
        """Queue every item of the tenant for re-embedding (e.g. after a domain config change)."""
        total = await self._session.scalar(
            select(func.count()).select_from(Item).where(Item.tenant_id == self._tenant.id)
        )
        batch = await self.create_batch(total or 0)
        await self._session.execute(
            update(Item)
            .where(Item.tenant_id == self._tenant.id)
            .values(
                embedding_status=EmbeddingStatus.PENDING, batch_id=batch.id, updated_at=utcnow()
            )
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return batch

    # --- Queries ---

    async def get_batch(self, batch_id: uuid.UUID) -> ItemBatch:
        batch = await self._session.scalar(
            select(ItemBatch).where(
                ItemBatch.id == batch_id, ItemBatch.tenant_id == self._tenant.id
            )
        )
        if batch is None:
            raise NotFoundError(f"Batch '{batch_id}' not found")
        return await refresh_batch(self._session, batch)

    async def list_items(
        self, *, page: int, status: EmbeddingStatus | None = None
    ) -> tuple[list[Item], int, int]:
        """Return (items, total, pages) for one page of the tenant's items, newest first."""
        conditions = [Item.tenant_id == self._tenant.id]
        if status is not None:
            conditions.append(Item.embedding_status == status)
        total = await self._session.scalar(
            select(func.count()).select_from(Item).where(*conditions)
        )
        result = await self._session.scalars(
            select(Item)
            .where(*conditions)
            .order_by(Item.created_at.desc(), Item.id)
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        return list(result.all()), total or 0, math.ceil((total or 0) / PAGE_SIZE)

    async def count_by_status(self) -> dict[EmbeddingStatus, int]:
        rows = await self._session.execute(
            select(Item.embedding_status, func.count())
            .where(Item.tenant_id == self._tenant.id)
            .group_by(Item.embedding_status)
        )
        counts = {status: 0 for status in EmbeddingStatus}
        counts.update(dict(rows.tuples().all()))
        return counts

    # --- Deletion ---

    async def delete_item(self, external_id: str, vector_store: PineconeService) -> None:
        """Delete from Pinecone first, so a Pinecone outage never leaves an orphan vector."""
        item = await self._session.scalar(
            select(Item).where(Item.tenant_id == self._tenant.id, Item.external_id == external_id)
        )
        if item is None:
            raise NotFoundError(f"Item '{external_id}' not found")
        if item.pinecone_id:
            try:
                deleted = await vector_store.delete_item(self._tenant.id, item.pinecone_id)
            except VectorStoreUnavailableError as exc:
                raise ServiceUnavailableError(f"Vector store unavailable: {exc}") from exc
            if not deleted:
                raise ServiceUnavailableError("Could not delete the vector from Pinecone; retry")
        await self._session.delete(item)
        await self._session.commit()

    @property
    def _dialect(self) -> str:
        return self._session.get_bind().dialect.name


def get_item_service(
    session: Annotated[AsyncSession, Depends(get_db)], auth: AuthDep
) -> ItemService:
    return ItemService(session, auth.tenant)


ItemServiceDep = Annotated[ItemService, Depends(get_item_service)]
