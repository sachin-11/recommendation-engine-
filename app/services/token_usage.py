"""Persisting embedding token usage per tenant and day."""

import logging
import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.metrics import EMBEDDING_TOKENS
from app.core.usage import EmbeddingUsage
from app.models.base import utcnow
from app.models.token_usage import TokenUsage, UsageSource

logger = logging.getLogger(__name__)


async def record_embedding_usage(
    session: AsyncSession, tenant_id: uuid.UUID, source: UsageSource, usage: EmbeddingUsage
) -> None:
    """Add `usage` to today's running totals. Never raises: usage accounting must not
    break an upload or a recommendation."""
    if usage.is_empty:
        return
    model = usage.model or settings.EMBEDDING_MODEL
    EMBEDDING_TOKENS.labels(source=source.value, model=model).inc(usage.tokens)
    table = cast(Table, TokenUsage.__table__)
    now = utcnow()
    values = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "day": datetime.now(UTC).date(),
        "source": source,
        "model": model,
        "tokens": usage.tokens,
        "api_calls": usage.api_calls,
        "texts": usage.texts,
        "cache_hits": usage.cache_hits,
        "created_at": now,
        "updated_at": now,
    }
    dialect = session.get_bind().dialect.name
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    stmt = insert(table).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "day", "source", "model"],
        set_={
            column: table.c[column] + stmt.excluded[column]
            for column in ("tokens", "api_calls", "texts", "cache_hits")
        }
        | {"updated_at": now},
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Could not record embedding usage for tenant %s", tenant_id)
