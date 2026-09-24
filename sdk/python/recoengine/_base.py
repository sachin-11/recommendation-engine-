"""Shared by the sync and async clients: configuration, retry policy, error mapping."""

from __future__ import annotations

import os
import random
from email.utils import parsedate_to_datetime
from time import time
from typing import Any

import httpx

from recoengine._version import __version__
from recoengine.exceptions import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RecoEngineError,
    ServiceUnavailableError,
    ValidationError,
)

DEFAULT_BASE_URL = "https://api.recoengine.io"
DEFAULT_TIMEOUT = 10.0
RETRY_STATUSES = frozenset({429, 503})


class ClientConfig:
    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        timeout: float,
        max_retries: int,
        retry_base_delay: float,
        max_retry_delay: float,
    ) -> None:
        api_key = api_key or os.environ.get("RECOENGINE_API_KEY")
        if not api_key:
            raise RecoEngineError(
                "api_key is required (or set RECOENGINE_API_KEY). "
                "Create one in the dashboard under API Keys."
            )
        base = base_url or os.environ.get("RECOENGINE_BASE_URL") or DEFAULT_BASE_URL
        self.base_url = base.rstrip("/") + "/api/v1"
        self.headers = {"X-API-Key": api_key, "User-Agent": f"recoengine-python/{__version__}"}
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.max_retry_delay = max_retry_delay

    def should_retry(self, response: httpx.Response, attempt: int) -> bool:
        return response.status_code in RETRY_STATUSES and attempt < self.max_retries

    def retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """Retry-After when sent, else exponential backoff with jitter; capped."""
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_after is not None:
            delay = retry_after
        else:
            delay = self.retry_base_delay * 2**attempt * (0.5 + random.random() / 2)
        return min(delay, self.max_retry_delay)


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP date)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time())
    except (TypeError, ValueError):
        return None


def raise_for_response(response: httpx.Response) -> None:
    """Raise the matching RecoEngineError for a non-2xx response."""
    if response.is_success:
        return
    try:
        body: Any = response.json()
    except ValueError:
        body = {}
    error = body.get("error", {}) if isinstance(body, dict) else {}
    message = error.get("message") or f"HTTP {response.status_code}"
    kwargs: dict[str, Any] = {
        "status_code": response.status_code,
        "code": error.get("code"),
        "details": error.get("details") or [],
        "request_id": response.headers.get("x-request-id"),
    }
    status = response.status_code
    if status in (401, 403):
        raise AuthError(message, **kwargs)
    if status == 404:
        raise NotFoundError(message, **kwargs)
    if status in (400, 409, 422):
        raise ValidationError(message, **kwargs)
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        raise RateLimitError(message, retry_after=retry_after, **kwargs)
    if status == 503:
        raise ServiceUnavailableError(message, **kwargs)
    raise RecoEngineError(message, **kwargs)


def recommend_body(
    top_k: int, filters: dict[str, Any] | None, include_raw_data: bool
) -> dict[str, Any]:
    return {"top_k": top_k, "filters": filters or {}, "include_raw_data": include_raw_data}
