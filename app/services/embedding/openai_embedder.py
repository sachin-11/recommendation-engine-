"""OpenAI embeddings with Redis caching, retry with backoff, and token truncation."""

import asyncio
import hashlib
import json
import logging
from functools import lru_cache
from typing import Any

import openai
from openai import AsyncOpenAI
from redis.asyncio import Redis
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# OpenAI accepts up to 2048 inputs per request; smaller requests keep retries cheap.
REQUEST_CHUNK_SIZE = 100
RETRY_ATTEMPTS = 5

_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


class EmbeddingUnavailableError(Exception):
    """OpenAI cannot be reached or rejects our credentials. Items should stay PENDING."""


class EmbeddingInputError(Exception):
    """OpenAI rejected the input itself. Retrying will not help; the item should FAIL."""


@lru_cache
def _encoding() -> Any | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # offline, or the encoding file cannot be downloaded
        logger.warning("tiktoken encoding unavailable; truncating by characters instead")
        return None


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    encoding = _encoding()
    if encoding is None:
        # About 4 characters per token for English; 3 keeps us safely under the limit.
        return text[: max_tokens * 3]
    tokens = encoding.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return text
    return str(encoding.decode(tokens[:max_tokens]))


class OpenAIEmbedder:
    def __init__(
        self,
        client: AsyncOpenAI | None,
        redis: Redis | None = None,
        *,
        model: str | None = None,
        dimension: int | None = None,
        max_tokens: int | None = None,
        cache_ttl: int | None = None,
        retry_attempts: int | None = None,
        request_timeout: float | None = None,
    ) -> None:
        self._client = client
        self._redis = redis
        self.model = model or settings.EMBEDDING_MODEL
        self.dimension = dimension or settings.EMBEDDING_DIMENSION
        self._max_tokens = max_tokens or settings.EMBEDDING_MAX_TOKENS
        self._cache_ttl = cache_ttl or settings.EMBEDDING_CACHE_TTL_SECONDS
        # Online queries use fewer retries and a timeout; ingestion can afford to wait.
        self._retry_attempts = retry_attempts
        self._request_timeout = request_timeout

    async def embed_text(self, text: str) -> list[float]:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed texts, keeping their order. Cached texts are not sent to OpenAI again."""
        if not texts:
            return []
        # Tokenising is CPU work, and the first call may download the encoding: keep it
        # off the event loop.
        prepared = await asyncio.to_thread(
            lambda: [truncate_to_tokens(text, self._max_tokens) for text in texts]
        )
        results = await self._cache_get([self._cache_key(text) for text in prepared])

        # Embed each distinct uncached text once, even if it repeats within the batch.
        uncached = list(
            dict.fromkeys(t for t, v in zip(prepared, results, strict=True) if v is None)
        )
        fresh: dict[str, list[float]] = {}
        for start in range(0, len(uncached), REQUEST_CHUNK_SIZE):
            chunk = uncached[start : start + REQUEST_CHUNK_SIZE]
            fresh.update(zip(chunk, await self._request(chunk), strict=True))
        await self._cache_set({self._cache_key(text): vec for text, vec in fresh.items()})

        return [v if v is not None else fresh[t] for t, v in zip(prepared, results, strict=True)]

    async def _request(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            raise EmbeddingUnavailableError("OPENAI_API_KEY is not configured")
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(_RETRYABLE),
                wait=wait_random_exponential(multiplier=1, max=30),
                stop=stop_after_attempt(
                    min(self._retry_attempts or RETRY_ATTEMPTS, RETRY_ATTEMPTS)
                ),
            ):
                with attempt:
                    response = await self._client.embeddings.create(
                        model=self.model,
                        input=texts,
                        dimensions=self.dimension,
                        timeout=self._request_timeout or openai.NOT_GIVEN,
                    )
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise EmbeddingUnavailableError(f"OpenAI unavailable after retries: {cause}") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise EmbeddingUnavailableError(f"OpenAI rejected the credentials: {exc}") from exc
        except (openai.BadRequestError, openai.UnprocessableEntityError) as exc:
            raise EmbeddingInputError(str(exc)) from exc
        except openai.APIError as exc:
            raise EmbeddingUnavailableError(f"OpenAI error: {exc}") from exc

        return [list(d.embedding) for d in sorted(response.data, key=lambda d: d.index)]

    # --- Cache ---

    def _cache_key(self, text: str) -> str:
        # Model and dimension are hashed in, so changing either never serves stale vectors.
        digest = hashlib.sha256(f"{self.model}:{self.dimension}\n{text}".encode()).hexdigest()
        return f"emb:{digest}"

    async def _cache_get(self, keys: list[str]) -> list[list[float] | None]:
        if self._redis is None:
            return [None] * len(keys)
        try:
            raw = await self._redis.mget(keys)
        except Exception:
            logger.warning("Embedding cache read failed; embedding without cache", exc_info=True)
            return [None] * len(keys)
        return [json.loads(value) if value else None for value in raw]

    async def _cache_set(self, entries: dict[str, list[float]]) -> None:
        if self._redis is None or not entries:
            return
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for key, vector in entries.items():
                    pipe.set(key, json.dumps(vector), ex=self._cache_ttl)
                await pipe.execute()
        except Exception:
            logger.warning("Embedding cache write failed", exc_info=True)


@lru_cache
def get_openai_client() -> AsyncOpenAI | None:
    """Process-wide client (it holds a connection pool). None when no key is configured."""
    if settings.OPENAI_API_KEY is None:
        return None
    # tenacity above owns retries, so the SDK's built-in retries are turned off.
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY.get_secret_value(), max_retries=0)
