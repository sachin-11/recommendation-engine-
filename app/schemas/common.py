"""Response schemas shared across endpoints."""

from typing import Literal

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str
    type: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


ComponentStatus = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    database: ComponentStatus
    redis: ComponentStatus


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "Resource not found"},
    422: {"model": ErrorResponse, "description": "Validation error"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}
