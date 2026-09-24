"""Pinecone vector storage. One serverless index per tenant.

The Pinecone SDK is synchronous, so every network call runs in a worker thread to keep
the event loop free.
"""

import asyncio
import json
import logging
import uuid
from functools import lru_cache
from typing import Any

from pinecone import Pinecone, ServerlessSpec

from app.core.config import settings

logger = logging.getLogger(__name__)

# Pinecone caps a request at 2 MB; 100 vectors of 1536 floats plus metadata stays well under.
UPSERT_CHUNK_SIZE = 100
INDEX_READY_TIMEOUT_SECONDS = 300


class VectorStoreUnavailableError(Exception):
    """Pinecone cannot be reached, or is not configured."""


def index_name_for(tenant_id: uuid.UUID | str) -> str:
    return f"reco-{str(tenant_id)[:8]}"


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Pinecone metadata allows strings, numbers, booleans and lists of strings; no nulls."""
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, bool | int | float | str):
            clean[key] = value
        elif isinstance(value, list | tuple | set):
            clean[key] = [str(v) for v in value if v is not None]
        else:
            clean[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return clean


class PineconeService:
    def __init__(
        self,
        client: Pinecone | None,
        *,
        cloud: str | None = None,
        region: str | None = None,
    ) -> None:
        self._client = client
        self._cloud = cloud or settings.PINECONE_CLOUD
        self._region = region or settings.PINECONE_ENVIRONMENT
        self._indexes: dict[str, Any] = {}
        self._known_indexes: set[str] = set()
        self._create_lock = asyncio.Lock()

    @property
    def _pc(self) -> Pinecone:
        if self._client is None:
            raise VectorStoreUnavailableError("PINECONE_API_KEY is not configured")
        return self._client

    async def ensure_index_exists(
        self, tenant_id: uuid.UUID | str, dimension: int | None = None
    ) -> str:
        """Create the tenant's index if missing and wait until it is ready. Returns its name."""
        name = index_name_for(tenant_id)
        if name in self._known_indexes:
            return name
        async with self._create_lock:
            if name in self._known_indexes:
                return name
            try:
                if not await asyncio.to_thread(self._pc.has_index, name):
                    logger.info("Creating Pinecone index %s", name)
                    await asyncio.to_thread(
                        self._pc.create_index,
                        name=name,
                        dimension=dimension or settings.EMBEDDING_DIMENSION,
                        metric="cosine",
                        spec=ServerlessSpec(cloud=self._cloud, region=self._region),
                        timeout=INDEX_READY_TIMEOUT_SECONDS,
                    )
            except VectorStoreUnavailableError:
                raise
            except Exception as exc:
                raise VectorStoreUnavailableError(f"Cannot prepare index {name}: {exc}") from exc
            self._known_indexes.add(name)
        return name

    async def upsert_item(
        self,
        tenant_id: uuid.UUID | str,
        pinecone_id: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> bool:
        result = await self.upsert_batch(
            tenant_id, [{"id": pinecone_id, "values": embedding, "metadata": metadata}]
        )
        return result["failed"] == 0

    async def upsert_batch(
        self, tenant_id: uuid.UUID | str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Upsert `{id, values, metadata}` dicts. A failed chunk is reported, not raised."""
        index = await self._index(tenant_id)
        upserted = 0
        failed_ids: list[str] = []
        for start in range(0, len(items), UPSERT_CHUNK_SIZE):
            chunk = items[start : start + UPSERT_CHUNK_SIZE]
            vectors = [
                {
                    "id": item["id"],
                    "values": item["values"],
                    "metadata": sanitize_metadata(item.get("metadata") or {}),
                }
                for item in chunk
            ]
            try:
                await asyncio.to_thread(index.upsert, vectors=vectors, show_progress=False)
                upserted += len(chunk)
            except Exception:
                logger.exception("Pinecone upsert failed for %d vectors", len(chunk))
                failed_ids.extend(item["id"] for item in chunk)
        return {"upserted": upserted, "failed": len(failed_ids), "failed_ids": failed_ids}

    async def delete_item(self, tenant_id: uuid.UUID | str, pinecone_id: str) -> bool:
        """Delete one vector. True when it is gone (including when the index never existed)."""
        name = index_name_for(tenant_id)
        try:
            if not await asyncio.to_thread(self._pc.has_index, name):
                return True
            index = await self._index(tenant_id)
            await asyncio.to_thread(index.delete, ids=[pinecone_id])
            return True
        except VectorStoreUnavailableError:
            raise
        except Exception:
            logger.exception("Pinecone delete failed for %s", pinecone_id)
            return False

    async def get_index_stats(self, tenant_id: uuid.UUID | str) -> dict[str, Any]:
        name = index_name_for(tenant_id)
        try:
            if not await asyncio.to_thread(self._pc.has_index, name):
                return {"index_name": name, "exists": False, "total_vector_count": 0}
            index = await self._index(tenant_id)
            stats = await asyncio.to_thread(index.describe_index_stats)
        except VectorStoreUnavailableError:
            raise
        except Exception as exc:
            raise VectorStoreUnavailableError(f"Cannot read stats for {name}: {exc}") from exc
        namespaces = getattr(stats, "namespaces", None) or {}
        return {
            "index_name": name,
            "exists": True,
            "total_vector_count": getattr(stats, "total_vector_count", 0) or 0,
            "dimension": getattr(stats, "dimension", None),
            "index_fullness": getattr(stats, "index_fullness", None),
            "namespaces": {
                ns: getattr(summary, "vector_count", 0) for ns, summary in namespaces.items()
            },
        }

    async def _index(self, tenant_id: uuid.UUID | str) -> Any:
        name = index_name_for(tenant_id)
        if name not in self._indexes:
            try:
                self._indexes[name] = await asyncio.to_thread(self._pc.Index, name=name)
            except VectorStoreUnavailableError:
                raise
            except Exception as exc:
                raise VectorStoreUnavailableError(f"Cannot open index {name}: {exc}") from exc
        return self._indexes[name]


@lru_cache
def get_pinecone_service() -> PineconeService:
    """Process-wide service, so index handles and the known-index cache are shared."""
    key = settings.PINECONE_API_KEY
    return PineconeService(Pinecone(api_key=key.get_secret_value()) if key else None)
