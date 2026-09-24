"""GET /metrics in Prometheus text format. Not in the public schema; block it at the proxy."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.metrics import ITEMS
from app.models.item import Item

metrics_router = APIRouter(include_in_schema=False)


@metrics_router.get("/metrics")
async def metrics(session: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    rows = await session.execute(
        select(Item.tenant_id, Item.embedding_status, func.count()).group_by(
            Item.tenant_id, Item.embedding_status
        )
    )
    # Rebuild the gauge so deleted tenants and emptied statuses disappear.
    ITEMS.clear()
    for tenant_id, status, count in rows.tuples():
        ITEMS.labels(tenant_id=str(tenant_id), status=status.value).set(count)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
