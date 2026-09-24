"""Errors raised by the SDK. Every one is a RecoEngineError."""

from __future__ import annotations

from typing import Any


class RecoEngineError(Exception):
    """Base class. `status_code` is None when the API could not be reached."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        #: API error code, e.g. "validation_error", "not_found".
        self.code = code
        self.details: list[dict[str, Any]] = details or []
        #: X-Request-ID of the failed request; quote it when contacting support.
        self.request_id = request_id

    def __str__(self) -> str:
        prefix = f"[{self.status_code}] " if self.status_code else ""
        return f"{prefix}{self.message}"


class AuthError(RecoEngineError):
    """401 or 403: missing, invalid, revoked or expired API key, or an inactive tenant."""


class NotFoundError(RecoEngineError):
    """404: the item, batch or query does not exist for this tenant."""


class ValidationError(RecoEngineError):
    """400, 409 or 422: the request was rejected; see `details`."""


class RateLimitError(RecoEngineError):
    """429 after all retries. `retry_after` is in seconds."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServiceUnavailableError(RecoEngineError):
    """503 after all retries: OpenAI or Pinecone is unavailable or timed out."""


class APIConnectionError(RecoEngineError):
    """The API could not be reached (DNS, connection refused, timeout)."""
