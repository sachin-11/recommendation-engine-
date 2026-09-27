"""GET /metrics in Prometheus text format. Not in the public schema; block it at the proxy."""

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_client.registry import Collector
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.base import utcnow
from app.models.item import EmbeddingStatus, Item

metrics_router = APIRouter(include_in_schema=False)


class _Snapshot(Collector):
    """Serves metric families already built from the database for this scrape."""

    def __init__(self, families: list[GaugeMetricFamily]) -> None:
        self._families = families

    def collect(self) -> Iterable[GaugeMetricFamily]:
        return self._families


def _process_metrics() -> bytes:
    """Counters and histograms: summed over all API processes in multiprocess mode."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return generate_latest(registry)
    return generate_latest(REGISTRY)


async def _database_metrics(session: AsyncSession) -> bytes:
    """Read from the database at scrape time, so every process reports the same values
    and deleted tenants or emptied statuses disappear."""
    items = GaugeMetricFamily(
        "items_total",
        "Items per tenant and embedding status (refreshed on each scrape).",
        labels=["tenant_id", "status"],
    )
    rows = await session.execute(
        select(Item.tenant_id, Item.embedding_status, func.count()).group_by(
            Item.tenant_id, Item.embedding_status
        )
    )
    for tenant_id, status, count in rows.tuples():
        items.add_metric([str(tenant_id), status.value], count)

    oldest = await session.scalar(
        select(func.min(Item.created_at)).where(Item.embedding_status == EmbeddingStatus.PENDING)
    )
    backlog_age = GaugeMetricFamily(
        "oldest_pending_item_age_seconds",
        "Age of the oldest PENDING item; 0 when nothing is waiting. "
        "Keeps growing when the worker is down or OpenAI/Pinecone keep failing.",
        value=_age_seconds(oldest),
    )

    registry = CollectorRegistry(auto_describe=False)
    registry.register(_Snapshot([items, backlog_age]))
    return generate_latest(registry)


def _age_seconds(created_at: datetime | None) -> float:
    if created_at is None:
        return 0.0
    if created_at.tzinfo is None:  # SQLite drops the timezone; values are stored in UTC
        created_at = created_at.replace(tzinfo=UTC)
    return max(0.0, (utcnow() - created_at).total_seconds())


@metrics_router.get("/metrics")
async def metrics(session: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    body = _process_metrics() + await _database_metrics(session)
    return Response(body, media_type=CONTENT_TYPE_LATEST)
