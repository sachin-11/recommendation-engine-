"""Item ingestion, batch and index request/response schemas."""

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.config import settings
from app.models.item import EmbeddingStatus
from app.models.item_batch import BatchStatus


class ItemIn(BaseModel):
    """One item. Besides `external_id`, any fields are accepted; the tenant's domain
    config decides which of them are embedded and which become filters."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "external_id": "job-101",
                "title": "Senior Python Developer",
                "description": "Build FastAPI services for our hiring platform.",
                "skills": ["Python", "FastAPI", "PostgreSQL"],
                "location": "Bangalore",
            }
        },
    )

    external_id: str = Field(min_length=1, max_length=255)

    @field_validator("external_id", mode="before")
    @classmethod
    def _coerce_external_id(cls, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value.strip() if isinstance(value, str) else value


class ItemUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    items: list[ItemIn] = Field(min_length=1)
    run_async: bool = Field(
        default=True,
        alias="async",
        description=(
            "true: queue a batch and return its id. "
            f"false: process up to {settings.MAX_SYNC_ITEMS} items before responding."
        ),
    )

    @model_validator(mode="after")
    def _within_request_limit(self) -> Self:
        if len(self.items) > settings.MAX_ITEMS_PER_REQUEST:
            raise ValueError(
                f"At most {settings.MAX_ITEMS_PER_REQUEST} items per request "
                f"(got {len(self.items)})"
            )
        return self


class ItemResultOut(BaseModel):
    external_id: str
    status: EmbeddingStatus
    error: str | None = None


class SyncUploadResponse(BaseModel):
    mode: Literal["sync"] = "sync"
    total_items: int
    succeeded: int
    failed: int
    results: list[ItemResultOut]


class BatchStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: uuid.UUID = Field(validation_alias=AliasChoices("id", "batch_id"))
    status: BatchStatus
    total_items: int
    processed_items: int = Field(description="Items embedded and stored in Pinecone.")
    failed_items: int
    created_at: datetime
    completed_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def progress_percentage(self) -> float:
        if self.status in (BatchStatus.DONE, BatchStatus.PARTIAL_FAIL) or not self.total_items:
            return 100.0
        return round(100 * (self.processed_items + self.failed_items) / self.total_items, 1)


class AsyncUploadResponse(BatchStatusResponse):
    mode: Literal["async"] = "async"
    status_url: str


class CsvUploadResponse(AsyncUploadResponse):
    column_mapping: dict[str, str] = Field(description="CSV header -> item field it was stored as.")


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    embedding_status: EmbeddingStatus
    pinecone_id: str | None
    batch_id: uuid.UUID | None
    raw_data: dict[str, Any]
    metadata: dict[str, Any] = Field(validation_alias="item_metadata")
    created_at: datetime
    updated_at: datetime


class ItemListResponse(BaseModel):
    items: list[ItemResponse]
    total: int
    page: int
    page_size: int
    pages: int


class IndexStatsResponse(BaseModel):
    index_name: str
    exists: bool
    total_vector_count: int
    dimension: int | None = None
    index_fullness: float | None = None
    namespaces: dict[str, int] = Field(default_factory=dict)
    items_by_status: dict[EmbeddingStatus, int] = Field(
        description="Item counts in the database, for comparison with the vector count."
    )
