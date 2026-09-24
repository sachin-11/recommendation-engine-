"""In-memory stand-ins for OpenAI and Pinecone, so tests make no network calls."""

import hashlib
import math
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import openai

from app.services.embedding.pinecone_service import (
    VectorStoreTimeoutError,
    VectorStoreUnavailableError,
    index_name_for,
    sanitize_metadata,
)

TEST_DIMENSION = 8
_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/embeddings")


def fake_vector(text: str, dimension: int = TEST_DIMENSION) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255 for b in digest[:dimension]]


@dataclass
class _Embedding:
    index: int
    embedding: list[float]


@dataclass
class _EmbeddingResponse:
    data: list[_Embedding]


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.down = False
        # Inputs containing this marker are rejected as invalid (a 400 from OpenAI).
        self.reject_marker: str | None = None

    async def create(
        self, *, model: str, input: list[str], dimensions: int, **_: Any
    ) -> _EmbeddingResponse:
        self.calls.append(list(input))
        if self.down:
            raise openai.APIConnectionError(request=_REQUEST)
        if self.reject_marker and any(self.reject_marker in text for text in input):
            raise openai.BadRequestError(
                "Invalid input",
                response=httpx.Response(400, request=_REQUEST),
                body=None,
            )
        # Returned out of order on purpose: callers must sort by index.
        data = [_Embedding(i, fake_vector(t, dimensions)) for i, t in enumerate(input)]
        return _EmbeddingResponse(data=list(reversed(data)))

    @property
    def embedded_texts(self) -> list[str]:
        return [text for call in self.calls for text in call]


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()


class FakeVectorStore:
    """Mimics PineconeService: one "index" per tenant, vectors keyed by id."""

    def __init__(self) -> None:
        self.indexes: dict[str, dict[str, dict[str, Any]]] = {}
        self.unavailable = False
        self.fail_ids: set[str] = set()
        self.query_times_out = False
        self.queries: list[dict[str, Any]] = []

    async def ensure_index_exists(
        self, tenant_id: uuid.UUID | str, dimension: int | None = None
    ) -> str:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        name = index_name_for(tenant_id)
        self.indexes.setdefault(name, {})
        return name

    async def upsert_batch(
        self, tenant_id: uuid.UUID | str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        index = self.indexes[index_name_for(tenant_id)]
        failed = [item["id"] for item in items if item["id"] in self.fail_ids]
        for item in items:
            if item["id"] not in self.fail_ids:
                index[item["id"]] = {
                    "values": item["values"],
                    "metadata": sanitize_metadata(item["metadata"]),
                }
        return {"upserted": len(items) - len(failed), "failed": len(failed), "failed_ids": failed}

    async def upsert_item(
        self,
        tenant_id: uuid.UUID | str,
        pinecone_id: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> bool:
        await self.ensure_index_exists(tenant_id)
        result = await self.upsert_batch(
            tenant_id, [{"id": pinecone_id, "values": embedding, "metadata": metadata}]
        )
        return result["failed"] == 0

    async def delete_item(self, tenant_id: uuid.UUID | str, pinecone_id: str) -> bool:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        self.indexes.get(index_name_for(tenant_id), {}).pop(pinecone_id, None)
        return True

    async def delete_items(self, tenant_id: uuid.UUID | str, pinecone_ids: list[str]) -> bool:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        index = self.indexes.get(index_name_for(tenant_id), {})
        for pinecone_id in pinecone_ids:
            index.pop(pinecone_id, None)
        return True

    async def delete_index(self, tenant_id: uuid.UUID | str) -> None:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        self.indexes.pop(index_name_for(tenant_id), None)

    async def get_index_stats(self, tenant_id: uuid.UUID | str) -> dict[str, Any]:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        name = index_name_for(tenant_id)
        if name not in self.indexes:
            return {"index_name": name, "exists": False, "total_vector_count": 0}
        count = len(self.indexes[name])
        return {
            "index_name": name,
            "exists": True,
            "total_vector_count": count,
            "dimension": TEST_DIMENSION,
            "index_fullness": 0.0,
            "namespaces": {"": count},
        }

    def vectors(self, tenant_id: uuid.UUID | str) -> dict[str, dict[str, Any]]:
        return self.indexes.get(index_name_for(tenant_id), {})

    async def query(
        self,
        tenant_id: uuid.UUID | str,
        *,
        top_k: int,
        vector: list[float] | None = None,
        id: str | None = None,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Brute-force cosine search with Pinecone's filter semantics."""
        self.queries.append({"top_k": top_k, "vector": vector, "id": id, "filter": filter})
        if self.query_times_out:
            raise VectorStoreTimeoutError("Pinecone query timed out after 5s (fake)")
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        index = self.vectors(tenant_id)
        if id is not None:
            if id not in index:
                return []
            vector = index[id]["values"]
        assert vector is not None
        scored = [
            {"id": vid, "score": _cosine(vector, v["values"]), "metadata": dict(v["metadata"])}
            for vid, v in index.items()
            if _passes(v["metadata"], filter or {})
        ]
        return sorted(scored, key=lambda m: m["score"], reverse=True)[:top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _passes(metadata: dict[str, Any], pinecone_filter: dict[str, Any]) -> bool:
    return all(
        _check(op, operand, metadata.get(field))
        for field, condition in pinecone_filter.items()
        for op, operand in condition.items()
    )


def _check(op: str, operand: Any, value: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    numeric = isinstance(value, int | float) and not isinstance(value, bool)
    if op == "$eq":
        return operand in values
    if op == "$ne":
        return operand not in values
    if op == "$in":
        return any(v in operand for v in values)
    if op == "$nin":
        return all(v not in operand for v in values)
    if not numeric:
        return False
    return {
        "$gt": value > operand,
        "$gte": value >= operand,
        "$lt": value < operand,
        "$lte": value <= operand,
    }[op]
