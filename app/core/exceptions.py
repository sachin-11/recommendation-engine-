"""Domain exceptions. Raised by the service layer; translated to HTTP responses in main.py."""

from typing import Any


class AppException(Exception):
    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers = headers


class BadRequestError(AppException):
    status_code = 400
    error_code = "bad_request"


class UnauthorizedError(AppException):
    status_code = 401
    error_code = "unauthorized"


class ForbiddenError(AppException):
    status_code = 403
    error_code = "forbidden"


class WorkspaceSuspendedError(ForbiddenError):
    """The workspace was suspended by a platform admin. The reason stays internal."""

    error_code = "workspace_suspended"

    def __init__(self) -> None:
        super().__init__("This workspace has been suspended. Contact support to restore access.")


class LimitExceededError(ForbiddenError):
    """A per-workspace limit set by a platform admin (items, monthly queries) is reached."""

    error_code = "limit_exceeded"


class NotFoundError(AppException):
    status_code = 404
    error_code = "not_found"


class ConflictError(AppException):
    status_code = 409
    error_code = "conflict"


class RateLimitError(AppException):
    status_code = 429
    error_code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, headers={"Retry-After": str(max(retry_after, 1))})


class ServiceUnavailableError(AppException):
    status_code = 503
    error_code = "service_unavailable"
