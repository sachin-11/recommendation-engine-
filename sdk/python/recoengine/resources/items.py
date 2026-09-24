"""Item upload, listing and deletion."""

from __future__ import annotations

import asyncio
import os
import time
from typing import IO, TYPE_CHECKING, Any, Dict, List, Union
from urllib.parse import quote

from recoengine.exceptions import RecoEngineError
from recoengine.models import (
    UPLOAD_RESULT,
    BatchStatus,
    CsvBatchResult,
    DeleteResult,
    EmbeddingStatus,
    Item,
    PaginatedItems,
    UploadResult,
)

if TYPE_CHECKING:
    from recoengine.client import AsyncRecoEngineClient, RecoEngineClient

CsvFile = Union[str, "os.PathLike[str]", bytes, IO[bytes]]


def _csv_part(file: CsvFile, filename: str | None) -> tuple[str, Any, str]:
    if isinstance(file, (str, os.PathLike)):
        path = os.fspath(file)
        with open(path, "rb") as handle:
            content: Any = handle.read()
        return (filename or os.path.basename(path), content, "text/csv")
    return (filename or "items.csv", file, "text/csv")


def _list_params(page: int, status: EmbeddingStatus | None, search: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page}
    if status:
        params["status"] = status
    if search:
        params["search"] = search
    return params


def _timed_out(batch: BatchStatus) -> RecoEngineError:
    return RecoEngineError(
        f"Batch {batch.batch_id} still {batch.status} "
        f"({batch.processed_items}/{batch.total_items}) after timeout"
    )


class Items:
    def __init__(self, client: RecoEngineClient) -> None:
        self._client = client

    def upload(self, items: List[Dict[str, Any]], *, async_: bool = True) -> UploadResult:
        """Upload up to 1000 items (each needs `external_id`).

        Returns a BatchResult to poll (default), or a SyncUploadResult with `async_=False`
        (at most 50 items). Re-uploading an external_id updates the item.
        """
        response = self._client._request(
            "POST", "/items/upload", json={"items": items, "async": async_}
        )
        return UPLOAD_RESULT.validate_python(response.json())

    def upload_csv(self, file: CsvFile, *, filename: str | None = None) -> CsvBatchResult:
        """Upload a CSV from a path, bytes or a binary file object. Always asynchronous."""
        response = self._client._request(
            "POST", "/items/upload-csv", files={"file": _csv_part(file, filename)}
        )
        return CsvBatchResult.model_validate(response.json())

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        response = self._client._request("GET", f"/items/batch/{quote(batch_id, safe='')}")
        return BatchStatus.model_validate(response.json())

    def wait_for_batch(
        self, batch_id: str, *, interval: float = 2.0, timeout: float = 300.0
    ) -> BatchStatus:
        """Poll until the batch is DONE or PARTIAL_FAIL."""
        deadline = time.monotonic() + timeout
        while True:
            batch = self.get_batch_status(batch_id)
            if batch.is_complete:
                return batch
            if time.monotonic() + interval > deadline:
                raise _timed_out(batch)
            time.sleep(interval)

    def get(self, external_id: str) -> Item:
        response = self._client._request("GET", f"/items/{quote(external_id, safe='')}")
        return Item.model_validate(response.json())

    def list(
        self,
        *,
        page: int = 1,
        status: EmbeddingStatus | None = None,
        search: str | None = None,
    ) -> PaginatedItems:
        response = self._client._request("GET", "/items", params=_list_params(page, status, search))
        return PaginatedItems.model_validate(response.json())

    def delete(self, external_id: str) -> None:
        """Delete one item from the database and the vector index."""
        self._client._request("DELETE", f"/items/{quote(external_id, safe='')}")

    def delete_many(self, external_ids: List[str]) -> DeleteResult:
        response = self._client._request(
            "POST", "/items/bulk-delete", json={"external_ids": external_ids}
        )
        return DeleteResult.model_validate(response.json())


class AsyncItems:
    def __init__(self, client: AsyncRecoEngineClient) -> None:
        self._client = client

    async def upload(self, items: List[Dict[str, Any]], *, async_: bool = True) -> UploadResult:
        response = await self._client._request(
            "POST", "/items/upload", json={"items": items, "async": async_}
        )
        return UPLOAD_RESULT.validate_python(response.json())

    async def upload_csv(self, file: CsvFile, *, filename: str | None = None) -> CsvBatchResult:
        response = await self._client._request(
            "POST", "/items/upload-csv", files={"file": _csv_part(file, filename)}
        )
        return CsvBatchResult.model_validate(response.json())

    async def get_batch_status(self, batch_id: str) -> BatchStatus:
        response = await self._client._request("GET", f"/items/batch/{quote(batch_id, safe='')}")
        return BatchStatus.model_validate(response.json())

    async def wait_for_batch(
        self, batch_id: str, *, interval: float = 2.0, timeout: float = 300.0
    ) -> BatchStatus:
        deadline = time.monotonic() + timeout
        while True:
            batch = await self.get_batch_status(batch_id)
            if batch.is_complete:
                return batch
            if time.monotonic() + interval > deadline:
                raise _timed_out(batch)
            await asyncio.sleep(interval)

    async def get(self, external_id: str) -> Item:
        response = await self._client._request("GET", f"/items/{quote(external_id, safe='')}")
        return Item.model_validate(response.json())

    async def list(
        self,
        *,
        page: int = 1,
        status: EmbeddingStatus | None = None,
        search: str | None = None,
    ) -> PaginatedItems:
        response = await self._client._request(
            "GET", "/items", params=_list_params(page, status, search)
        )
        return PaginatedItems.model_validate(response.json())

    async def delete(self, external_id: str) -> None:
        await self._client._request("DELETE", f"/items/{quote(external_id, safe='')}")

    async def delete_many(self, external_ids: List[str]) -> DeleteResult:
        response = await self._client._request(
            "POST", "/items/bulk-delete", json={"external_ids": external_ids}
        )
        return DeleteResult.model_validate(response.json())
