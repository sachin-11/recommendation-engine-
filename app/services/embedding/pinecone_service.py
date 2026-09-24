"""Pinecone vector storage: one shared serverless index, one namespace per tenant.

Namespaces isolate tenants (every read and write is scoped to the tenant's namespace) while
keeping a single index, so the number of tenants is not limited by Pinecone's per-project
index quota. The Pinecone SDK is synchronous, so every call runs in a worker thread to keep
the event loop free.
"""

import asyncio
import json
import logging
import re
import uuid
from functools import lru_cache
from typing import Any

from pinecone import NotFoundError as PineconeNotFoundError
from pinecone import Pinecone, ServerlessSpec

from app.core.config import settings

logger = logging.getLogger(__name__)

# Pinecone caps a request at 2 MB; 100 vectors of 1536 floats plus metadata stays well under.
UPSERT_CHUNK_SIZE = 100
DELETE_CHUNK_SIZE = 1000
INDEX_READY_TIMEOUT_SECONDS = 300
# Upper bound for one similarity query, including the index lookup on first use.
QUERY_TIMEOUT_SECONDS = 5.0
# Indexes created before namespaces were introduced: reco-<first 8 hex chars of tenant id>.
LEGACY_INDEX_PATTERN = re.compile(r"^reco-[0-9a-f]{8}$")


class VectorStoreUnavailableError(Exception):
    """Pinecone cannot be reached, or is not configured."""


class VectorStoreTimeoutError(VectorStoreUnavailableError):
    """A query did not finish within QUERY_TIMEOUT_SECONDS."""


class IndexNotFoundError(VectorStoreUnavailableError):
    """The shared index does not exist yet (nothing was ever embedded)."""


class VectorStoreQueryError(Exception):
    """Pinecone rejected the query itself, e.g. a filter that does not fit the stored data."""


def namespace_for(tenant_id: uuid.UUID | str) -> str:
    return str(tenant_id)


def legacy_index_name_for(tenant_id: uuid.UUID | str) -> str:
    """Name of the per-tenant index used before namespaces (for migration only)."""
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
        index_name: str | None = None,
        cloud: str | None = None,
        region: str | None = None,
        query_timeout: float = QUERY_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self.index_name = index_name or settings.PINECONE_INDEX_NAME
        self.query_timeout = query_timeout
        self._cloud = cloud or settings.PINECONE_CLOUD
        self._region = region or settings.PINECONE_ENVIRONMENT
        self._index_handle: Any = None
        self._index_ready = False
        self._create_lock = asyncio.Lock()

    @property
    def _pc(self) -> Pinecone:
        if self._client is None:
            raise VectorStoreUnavailableError("PINECONE_API_KEY is not configured")
        return self._client

    # --- Index lifecycle ---

    async def ensure_index_exists(
        self, tenant_id: uuid.UUID | str | None = None, dimension: int | None = None
    ) -> str:
        """Create the shared index if missing and wait until it is ready. Returns its name.

        `tenant_id` is accepted for call-site symmetry; tenants need no setup of their own,
        a namespace appears with its first vector.
        """
        if self._index_ready:
            return self.index_name
        async with self._create_lock:
            # Another task may have created it while this one waited for the lock.
            if not self._index_ready:
                await self._create_index_if_missing(dimension)
                self._index_ready = True
        return self.index_name

    async def _create_index_if_missing(self, dimension: int | None) -> None:
        try:
            if not await asyncio.to_thread(self._pc.has_index, self.index_name):
                logger.info("Creating Pinecone index %s", self.index_name)
                await asyncio.to_thread(
                    self._pc.create_index,
                    name=self.index_name,
                    dimension=dimension or settings.EMBEDDING_DIMENSION,
                    metric="cosine",
                    spec=ServerlessSpec(cloud=self._cloud, region=self._region),
                    timeout=INDEX_READY_TIMEOUT_SECONDS,
                )
        except VectorStoreUnavailableError:
            raise
        except Exception as exc:
            raise VectorStoreUnavailableError(
                f"Cannot prepare index {self.index_name}: {exc}"
            ) from exc

    # --- Writes ---

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
        return bool(result["failed"] == 0)

    async def upsert_batch(
        self, tenant_id: uuid.UUID | str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Upsert `{id, values, metadata}` dicts into the tenant's namespace.
        A failed chunk is reported, not raised."""
        index = await self._index()
        namespace = namespace_for(tenant_id)
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
                await asyncio.to_thread(
                    index.upsert, vectors=vectors, namespace=namespace, show_progress=False
                )
                upserted += len(chunk)
            except Exception:
                logger.exception("Pinecone upsert failed for %d vectors", len(chunk))
                failed_ids.extend(item["id"] for item in chunk)
        return {"upserted": upserted, "failed": len(failed_ids), "failed_ids": failed_ids}

    async def delete_item(self, tenant_id: uuid.UUID | str, pinecone_id: str) -> bool:
        """Delete one vector. True when it is gone (including when it never existed)."""
        return await self.delete_items(tenant_id, [pinecone_id])

    async def delete_items(self, tenant_id: uuid.UUID | str, pinecone_ids: list[str]) -> bool:
        """Delete vectors from the tenant's namespace. True when all of them are gone."""
        if not pinecone_ids:
            return True
        namespace = namespace_for(tenant_id)
        try:
            index = await self._index()
            for start in range(0, len(pinecone_ids), DELETE_CHUNK_SIZE):
                await asyncio.to_thread(
                    index.delete,
                    ids=pinecone_ids[start : start + DELETE_CHUNK_SIZE],
                    namespace=namespace,
                )
            return True
        except (IndexNotFoundError, PineconeNotFoundError):
            return True  # no index or no namespace: nothing to delete
        except VectorStoreUnavailableError:
            raise
        except Exception:
            logger.exception("Pinecone delete failed for %d vectors", len(pinecone_ids))
            return False

    async def delete_tenant_vectors(self, tenant_id: uuid.UUID | str) -> None:
        """Remove the tenant's whole namespace (used when the tenant is deleted)."""
        try:
            index = await self._index()
            await asyncio.to_thread(
                index.delete, delete_all=True, namespace=namespace_for(tenant_id)
            )
        except (IndexNotFoundError, PineconeNotFoundError):
            return
        except VectorStoreUnavailableError:
            raise
        except Exception as exc:
            raise VectorStoreUnavailableError(
                f"Cannot delete vectors of tenant {tenant_id}: {exc}"
            ) from exc

    # --- Reads ---

    async def get_index_stats(self, tenant_id: uuid.UUID | str) -> dict[str, Any]:
        """Stats for the tenant's namespace only; other tenants' namespaces are never exposed."""
        namespace = namespace_for(tenant_id)
        empty = {"index_name": self.index_name, "namespace": namespace, "total_vector_count": 0}
        try:
            index = await self._index()
            stats = await asyncio.to_thread(index.describe_index_stats)
        except IndexNotFoundError:
            return {**empty, "exists": False}
        except VectorStoreUnavailableError:
            raise
        except Exception as exc:
            raise VectorStoreUnavailableError(f"Cannot read index stats: {exc}") from exc
        summary = (getattr(stats, "namespaces", None) or {}).get(namespace)
        count = int(getattr(summary, "vector_count", 0) or 0) if summary is not None else 0
        return {
            **empty,
            "exists": True,
            "total_vector_count": count,
            "dimension": getattr(stats, "dimension", None),
            "index_fullness": getattr(stats, "index_fullness", None),
            "namespaces": {namespace: count},
        }

    async def query(
        self,
        tenant_id: uuid.UUID | str,
        *,
        top_k: int,
        vector: list[float] | None = None,
        id: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Nearest neighbours of `vector`, or of the stored vector with this `id` (one round
        trip instead of fetch + query), within the tenant's namespace. Returns
        `{id, score, metadata}` dicts, best first. No index yet means nothing to recommend."""
        timeout = self.query_timeout
        try:
            async with asyncio.timeout(timeout):
                index = await self._index()
                response = await asyncio.to_thread(
                    index.query,
                    top_k=top_k,
                    vector=vector,
                    id=id,
                    filter=filter or None,
                    namespace=namespace_for(tenant_id),
                    include_metadata=True,
                    timeout=timeout,
                )
        except (IndexNotFoundError, PineconeNotFoundError):
            return []
        except TimeoutError as exc:  # also PineconeTimeoutError, a TimeoutError subclass
            raise VectorStoreTimeoutError(f"Pinecone query timed out after {timeout:.0f}s") from exc
        except VectorStoreUnavailableError:
            raise
        except Exception as exc:
            if 400 <= (getattr(exc, "status_code", None) or 0) < 500:
                raise VectorStoreQueryError(str(exc)) from exc
            raise VectorStoreUnavailableError(f"Pinecone query failed: {exc}") from exc
        return [
            {"id": m.id, "score": float(m.score), "metadata": dict(m.metadata or {})}
            for m in response.matches
        ]

    async def warm_up(self) -> None:
        """Open the index handle and its connection, so the first query does not pay for
        the index lookup and TLS setup. Failures are ignored."""
        try:
            index = await self._index()
            await asyncio.to_thread(index.describe_index_stats)
        except Exception as exc:
            logger.info("Pinecone warm-up skipped: %s", exc)

    # --- Legacy per-tenant indexes (migration only) ---

    async def list_legacy_indexes(self) -> list[str]:
        names = await asyncio.to_thread(lambda: [i.name for i in self._pc.list_indexes()])
        return sorted(n for n in names if LEGACY_INDEX_PATTERN.match(n) and n != self.index_name)

    async def delete_legacy_index(self, name: str) -> None:
        if not LEGACY_INDEX_PATTERN.match(name) or name == self.index_name:
            raise ValueError(f"{name} is not a legacy per-tenant index")
        await asyncio.to_thread(self._pc.delete_index, name)

    async def _index(self) -> Any:
        if self._index_handle is None:
            try:
                self._index_handle = await asyncio.to_thread(self._pc.Index, name=self.index_name)
            except VectorStoreUnavailableError:
                raise
            except PineconeNotFoundError as exc:
                raise IndexNotFoundError(f"Index {self.index_name} does not exist") from exc
            except Exception as exc:
                raise VectorStoreUnavailableError(
                    f"Cannot open index {self.index_name}: {exc}"
                ) from exc
        return self._index_handle


@lru_cache
def get_pinecone_service() -> PineconeService:
    """Process-wide service, so the index handle is shared."""
    key = settings.PINECONE_API_KEY
    return PineconeService(Pinecone(api_key=key.get_secret_value()) if key else None)
