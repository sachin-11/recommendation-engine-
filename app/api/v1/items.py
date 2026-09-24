"""Tenant-facing item ingestion and index routes. All require an X-API-Key header."""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Response, UploadFile, status

from app.core.config import settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.middleware.auth import AuthDep, authenticate
from app.middleware.rate_limit import RateLimiterDep, enforce_request_rate
from app.models.item import EmbeddingStatus
from app.models.item_batch import ItemBatch
from app.schemas.account import BulkDeleteRequest, BulkDeleteResponse
from app.schemas.common import ERROR_RESPONSES, ErrorResponse
from app.schemas.item import (
    AsyncUploadResponse,
    BatchStatusResponse,
    CsvUploadResponse,
    IndexStatsResponse,
    ItemListResponse,
    ItemResponse,
    ItemResultOut,
    ItemUploadRequest,
    SyncUploadResponse,
)
from app.services.csv_import import parse_items_csv
from app.services.embedding.dependencies import PipelineDep, VectorStoreDep
from app.services.embedding.pinecone_service import VectorStoreUnavailableError
from app.services.embedding.pipeline import EmbeddingPipeline
from app.services.item_service import PAGE_SIZE, ItemServiceDep

logger = logging.getLogger(__name__)

AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    **ERROR_RESPONSES,
    401: {"model": ErrorResponse, "description": "Missing, invalid or expired API key"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded (see Retry-After)"},
}
# Every tenant-facing router: API key auth, then the per-key request rate limit.
PROTECTED = [Depends(authenticate), Depends(enforce_request_rate)]

items_router = APIRouter(
    prefix="/items", tags=["items"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)
index_router = APIRouter(
    prefix="/index", tags=["index"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)


async def run_batch(pipeline: EmbeddingPipeline, batch_id: uuid.UUID) -> None:
    """Background task body. Never raises: a failure here has no client to report to."""
    try:
        result = await pipeline.process_batch(batch_id)
        logger.info(
            "Batch %s: %s (%d done, %d failed of %d)",
            batch_id,
            result.status,
            result.processed_items,
            result.failed_items,
            result.total_items,
        )
    except Exception:
        logger.exception("Background processing of batch %s crashed", batch_id)


def _queued(batch: ItemBatch, **extra: object) -> dict[str, object]:
    base = BatchStatusResponse.model_validate(batch).model_dump()
    return {**base, "status_url": f"/api/v1/items/batch/{batch.id}", **extra}


# --- Items ---


@items_router.post(
    "/upload",
    summary="Upload items (JSON)",
    responses={
        202: {"model": AsyncUploadResponse, "description": "Queued (async=true)"},
        503: {"model": ErrorResponse, "description": "Embedding service down; items kept PENDING"},
    },
)
async def upload_items(
    payload: ItemUploadRequest,
    auth: AuthDep,
    service: ItemServiceDep,
    pipeline: PipelineDep,
    limiter: RateLimiterDep,
    background_tasks: BackgroundTasks,
    response: Response,
) -> SyncUploadResponse | AsyncUploadResponse:
    items = [item.model_dump() for item in payload.items]
    if not payload.run_async and len(items) > settings.MAX_SYNC_ITEMS:
        raise BadRequestError(
            f"async=false supports at most {settings.MAX_SYNC_ITEMS} items; "
            f"use async=true for {len(items)}"
        )
    await limiter.consume_items(auth.tenant.id, len(items))

    if payload.run_async:
        batch = await service.ingest_async(items)
        background_tasks.add_task(run_batch, pipeline, batch.id)
        response.status_code = status.HTTP_202_ACCEPTED
        return AsyncUploadResponse.model_validate(_queued(batch))

    results = await service.ingest_sync(items, pipeline)
    succeeded = sum(r.status is EmbeddingStatus.DONE for r in results)
    return SyncUploadResponse(
        total_items=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=[
            ItemResultOut(external_id=r.external_id, status=r.status, error=r.error)
            for r in results
        ],
    )


@items_router.post(
    "/upload-csv",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload items from a CSV file (always async)",
    responses={400: {"model": ErrorResponse, "description": "Invalid CSV, with column hints"}},
)
async def upload_items_csv(
    auth: AuthDep,
    service: ItemServiceDep,
    pipeline: PipelineDep,
    limiter: RateLimiterDep,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="UTF-8 CSV with a header row")],
) -> CsvUploadResponse:
    content = await file.read(settings.MAX_CSV_BYTES + 1)
    if len(content) > settings.MAX_CSV_BYTES:
        raise BadRequestError(f"CSV is larger than {settings.MAX_CSV_BYTES // (1024 * 1024)} MB")
    parsed = parse_items_csv(content, auth.tenant.domain_config)
    await limiter.consume_items(auth.tenant.id, len(parsed.items))

    batch = await service.ingest_async(parsed.items)
    background_tasks.add_task(run_batch, pipeline, batch.id)
    return CsvUploadResponse.model_validate(_queued(batch, column_mapping=parsed.column_mapping))


@items_router.get("/batch/{batch_id}", summary="Batch progress")
async def get_batch_status(batch_id: uuid.UUID, service: ItemServiceDep) -> BatchStatusResponse:
    return BatchStatusResponse.model_validate(await service.get_batch(batch_id))


@items_router.get("", summary="List items (20 per page)")
async def list_items(
    service: ItemServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    status_filter: Annotated[EmbeddingStatus | None, Query(alias="status")] = None,
    search: Annotated[
        str | None, Query(max_length=255, description="Part of an external_id, any case")
    ] = None,
) -> ItemListResponse:
    items, total, pages = await service.list_items(page=page, status=status_filter, search=search)
    return ItemListResponse(
        items=[ItemResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=PAGE_SIZE,
        pages=pages,
    )


@items_router.post(
    "/bulk-delete",
    summary="Delete up to 1000 items from the database and Pinecone",
    responses={
        503: {"model": ErrorResponse, "description": "Pinecone unavailable; nothing deleted"}
    },
)
async def bulk_delete_items(
    payload: BulkDeleteRequest, service: ItemServiceDep, vector_store: VectorStoreDep
) -> BulkDeleteResponse:
    deleted, not_found = await service.delete_items(payload.external_ids, vector_store)
    return BulkDeleteResponse(deleted=deleted, not_found=not_found)


@items_router.delete(
    "",
    summary="Delete ALL of this tenant's items from the database and Pinecone",
    responses={
        503: {"model": ErrorResponse, "description": "Pinecone unavailable; nothing deleted"}
    },
)
async def delete_all_items(
    service: ItemServiceDep, vector_store: VectorStoreDep
) -> BulkDeleteResponse:
    deleted, _ = await service.delete_items(None, vector_store)
    return BulkDeleteResponse(deleted=deleted, not_found=[])


@items_router.get("/{external_id}", summary="One item, with its embedding status and metadata")
async def get_item(external_id: str, service: ItemServiceDep) -> ItemResponse:
    return ItemResponse.model_validate(await service.get_item(external_id))


@items_router.delete(
    "/{external_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an item from the database and Pinecone",
    responses={
        503: {"model": ErrorResponse, "description": "Pinecone unavailable; nothing deleted"}
    },
)
async def delete_item(
    external_id: str, service: ItemServiceDep, vector_store: VectorStoreDep
) -> None:
    await service.delete_item(external_id, vector_store)


# --- Index ---


@index_router.get(
    "/stats",
    summary="Pinecone index stats for this tenant",
    responses={503: {"model": ErrorResponse, "description": "Pinecone unavailable"}},
)
async def index_stats(
    auth: AuthDep, service: ItemServiceDep, vector_store: VectorStoreDep
) -> IndexStatsResponse:
    try:
        stats = await vector_store.get_index_stats(auth.tenant.id)
    except VectorStoreUnavailableError as exc:
        raise ServiceUnavailableError(f"Vector store unavailable: {exc}") from exc
    return IndexStatsResponse(**stats, items_by_status=await service.count_by_status())


@index_router.post(
    "/rebuild",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-embed every item of this tenant (always async)",
)
async def rebuild_index(
    service: ItemServiceDep, pipeline: PipelineDep, background_tasks: BackgroundTasks
) -> AsyncUploadResponse:
    batch = await service.rebuild()
    if batch.total_items:
        background_tasks.add_task(run_batch, pipeline, batch.id)
    return AsyncUploadResponse.model_validate(_queued(batch))
