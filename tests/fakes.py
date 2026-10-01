"""In-memory stand-ins for OpenAI and Pinecone, so tests make no network calls."""

import asyncio
import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import openai

from app.services.embedding.pinecone_service import (
    VectorStoreTimeoutError,
    VectorStoreUnavailableError,
    namespace_for,
    sanitize_metadata,
)
from app.services.recommendation.filter_builder import matches_filter

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
class _Usage:
    prompt_tokens: int
    total_tokens: int


@dataclass
class _EmbeddingResponse:
    data: list[_Embedding]
    usage: _Usage


def fake_tokens(texts: list[str]) -> int:
    """Deterministic stand-in for OpenAI's token count: one token per word."""
    return sum(len(text.split()) for text in texts)


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
        tokens = fake_tokens(input)
        return _EmbeddingResponse(data=list(reversed(data)), usage=_Usage(tokens, tokens))

    @property
    def embedded_texts(self) -> list[str]:
        return [text for call in self.calls for text in call]


@dataclass
class _Message:
    content: str | None


@dataclass
class _Choice:
    message: _Message


@dataclass
class _ChatUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _ChatResponse:
    choices: list[_Choice]
    usage: _ChatUsage


_PROMPT_ITEM = re.compile(r"^\[(\d+)\] (.*)$", re.MULTILINE)
_PROMPT_QUERY = re.compile(r"^Query: (.*)$", re.MULTILINE)


class FakeChatCompletions:
    """Stand-in for the LLM reranker's chat model: scores each item by how many query
    words it contains (3 points each, at most 10), unless `scores` overrides by item
    text substring."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.down = False
        self.delay = 0.0
        self.content: str | None = None  # a fixed (e.g. malformed) answer
        self.scores: dict[str, int] = {}  # item text substring -> score

    async def create(
        self, *, model: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> _ChatResponse:
        self.calls.append({"model": model, "messages": messages, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.down:
            raise openai.APIConnectionError(request=_REQUEST)
        prompt = messages[-1]["content"]
        if self.content is not None:
            content = self.content
        else:
            query_match = _PROMPT_QUERY.search(prompt)
            words = set(json.loads(query_match.group(1)).lower().split()) if query_match else set()
            items = []
            for number, raw in _PROMPT_ITEM.findall(prompt):
                text = json.loads(raw)
                score = next(
                    (v for k, v in self.scores.items() if k.lower() in text.lower()),
                    min(10, 3 * sum(w in text.lower() for w in words)),
                )
                items.append(
                    {"id": number, "score": score, "reason": f"matches {score // 3} words"}
                )
            content = json.dumps({"items": items})
        tokens = len(prompt.split())
        return _ChatResponse(
            [_Choice(_Message(content))], _ChatUsage(tokens, 10 * tokens // 100 + 5)
        )


class FakeChat:
    def __init__(self) -> None:
        self.completions = FakeChatCompletions()


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()
        self.chat = FakeChat()


class FakeVectorStore:
    """Mimics PineconeService: one shared index, a namespace per tenant, vectors keyed by id."""

    INDEX_NAME = "reco-shared"

    def __init__(self) -> None:
        # namespace (tenant id) -> vector id -> {values, metadata}
        self.namespaces: dict[str, dict[str, dict[str, Any]]] = {}
        self.index_created = False
        self.unavailable = False
        self.fail_ids: set[str] = set()
        self.query_times_out = False
        self.queries: list[dict[str, Any]] = []
        self.fetches: list[list[str]] = []

    async def ensure_index_exists(
        self, tenant_id: uuid.UUID | str | None = None, dimension: int | None = None
    ) -> str:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        self.index_created = True
        return self.INDEX_NAME

    async def upsert_batch(
        self, tenant_id: uuid.UUID | str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        assert self.index_created, "upsert before ensure_index_exists"
        index = self.namespaces.setdefault(namespace_for(tenant_id), {})
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
        self.namespaces.get(namespace_for(tenant_id), {}).pop(pinecone_id, None)
        return True

    async def delete_items(self, tenant_id: uuid.UUID | str, pinecone_ids: list[str]) -> bool:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        index = self.namespaces.get(namespace_for(tenant_id), {})
        for pinecone_id in pinecone_ids:
            index.pop(pinecone_id, None)
        return True

    async def delete_tenant_vectors(self, tenant_id: uuid.UUID | str) -> None:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        self.namespaces.pop(namespace_for(tenant_id), None)

    async def get_index_stats(self, tenant_id: uuid.UUID | str) -> dict[str, Any]:
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        namespace = namespace_for(tenant_id)
        base = {"index_name": self.INDEX_NAME, "namespace": namespace}
        if not self.index_created:
            return {**base, "exists": False, "total_vector_count": 0}
        count = len(self.namespaces.get(namespace, {}))
        return {
            **base,
            "exists": True,
            "total_vector_count": count,
            "dimension": TEST_DIMENSION,
            "index_fullness": 0.0,
            "namespaces": {namespace: count},
        }

    def vectors(self, tenant_id: uuid.UUID | str) -> dict[str, dict[str, Any]]:
        return self.namespaces.get(namespace_for(tenant_id), {})

    async def fetch_vectors(
        self, tenant_id: uuid.UUID | str, ids: list[str]
    ) -> dict[str, list[float]]:
        self.fetches.append(ids)
        if self.unavailable:
            raise VectorStoreUnavailableError("Pinecone is down (fake)")
        index = self.vectors(tenant_id)
        return {vid: list(index[vid]["values"]) for vid in ids if vid in index}

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
            if matches_filter(v["metadata"], filter or {})
        ]
        return sorted(scored, key=lambda m: m["score"], reverse=True)[:top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
