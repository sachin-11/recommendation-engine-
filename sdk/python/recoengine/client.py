"""Sync and async clients for the RecoEngine API."""

from __future__ import annotations

import asyncio
import time
from types import TracebackType
from typing import Any, Awaitable, Callable

import httpx

from recoengine._base import DEFAULT_TIMEOUT, ClientConfig, raise_for_response
from recoengine.exceptions import APIConnectionError
from recoengine.resources.analytics import Analytics, AsyncAnalytics
from recoengine.resources.items import AsyncItems, Items
from recoengine.resources.recommend import AsyncRecommend, Recommend


class RecoEngineClient:
    """Synchronous client.

    >>> client = RecoEngineClient(api_key="reco_…")
    >>> results = client.recommend.by_text("senior python developer", top_k=5)

    `api_key` and `base_url` fall back to RECOENGINE_API_KEY and RECOENGINE_BASE_URL.
    429 and 503 responses are retried `max_retries` times, honouring Retry-After.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        max_retry_delay: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = ClientConfig(
            api_key, base_url, timeout, max_retries, retry_base_delay, max_retry_delay
        )
        self._http = httpx.Client(
            base_url=self._config.base_url,
            headers=self._config.headers,
            timeout=timeout,
            transport=transport,
        )
        self._sleep = sleep
        self.items = Items(self)
        self.recommend = Recommend(self)
        self.analytics = Analytics(self)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                raise APIConnectionError(f"Could not reach the RecoEngine API: {exc}") from exc
            if self._config.should_retry(response, attempt):
                self._sleep(self._config.retry_delay(response, attempt))
                attempt += 1
                continue
            raise_for_response(response)
            return response

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RecoEngineClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncRecoEngineClient:
    """Asynchronous client with the same methods as RecoEngineClient, as coroutines.

    >>> async with AsyncRecoEngineClient(api_key="reco_…") as client:
    ...     results = await client.recommend.by_text("spicy vegetarian pasta")
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        max_retry_delay: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = ClientConfig(
            api_key, base_url, timeout, max_retries, retry_base_delay, max_retry_delay
        )
        self._http = httpx.AsyncClient(
            base_url=self._config.base_url,
            headers=self._config.headers,
            timeout=timeout,
            transport=transport,
        )
        self._sleep = sleep
        self.items = AsyncItems(self)
        self.recommend = AsyncRecommend(self)
        self.analytics = AsyncAnalytics(self)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                raise APIConnectionError(f"Could not reach the RecoEngine API: {exc}") from exc
            if self._config.should_retry(response, attempt):
                await self._sleep(self._config.retry_delay(response, attempt))
                attempt += 1
                continue
            raise_for_response(response)
            return response

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncRecoEngineClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
