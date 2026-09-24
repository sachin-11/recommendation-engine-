"""Collects OpenAI embedding usage (tokens, calls, cache hits) for the current unit of work.

The caller that knows the tenant (the ingestion pipeline, a recommendation query) opens a
scope; the embedder, which does not know the tenant, adds to whatever scope is open. The
scope object is shared by tasks started inside it (asyncio.gather), so batch queries add up.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class EmbeddingUsage:
    tokens: int = 0
    api_calls: int = 0
    texts: int = 0
    cache_hits: int = 0
    model: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.texts == 0 and self.api_calls == 0


_current: ContextVar[EmbeddingUsage | None] = ContextVar("embedding_usage", default=None)


@contextmanager
def track_embedding_usage() -> Iterator[EmbeddingUsage]:
    usage = EmbeddingUsage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def add_embedding_usage(
    *,
    tokens: int = 0,
    api_calls: int = 0,
    texts: int = 0,
    cache_hits: int = 0,
    model: str | None = None,
) -> None:
    usage = _current.get()
    if usage is None:
        return
    usage.tokens += tokens
    usage.api_calls += api_calls
    usage.texts += texts
    usage.cache_hits += cache_hits
    if model:
        usage.model = model
