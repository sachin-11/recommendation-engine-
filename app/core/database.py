"""Async SQLAlchemy engine, session factory and FastAPI session dependency."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)


def _engine_options(url: str) -> dict[str, Any]:
    options: dict[str, Any] = {"echo": settings.DB_ECHO, "pool_pre_ping": True}
    if url.startswith("postgresql"):
        options.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=1800,
        )
    return options


engine: AsyncEngine = create_async_engine(settings.DATABASE_URL, **_engine_options(settings.DATABASE_URL))

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a session per request; roll back if the request raised."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping_database() -> None:
    """Raise if the database is unreachable. Used at startup to fail fast."""
    async with engine.connect() as connection:
        await connection.execute(select(1))


async def check_database(session: AsyncSession) -> bool:
    """Health-check probe: True when a trivial query succeeds within the timeout."""
    try:
        async with asyncio.timeout(settings.HEALTH_CHECK_TIMEOUT_SECONDS):
            await session.execute(select(1))
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False
