"""Domain exceptions. Raised by the service layer; translated to HTTP responses in main.py."""

from typing import Any


class AppException(Exception):
    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class BadRequestError(AppException):
    status_code = 400
    error_code = "bad_request"


class ForbiddenError(AppException):
    status_code = 403
    error_code = "forbidden"


class NotFoundError(AppException):
    status_code = 404
    error_code = "not_found"


class ConflictError(AppException):
    status_code = 409
    error_code = "conflict"


class ServiceUnavailableError(AppException):
    status_code = 503
    error_code = "service_unavailable"
