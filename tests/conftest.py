"""Test fixtures.

Tests run against an in-memory SQLite DB and fakeredis by default, so they need no
running services. Set TEST_DATABASE_URL (postgresql+asyncpg://...) to run them
against a real Postgres instead. Note: tables are dropped after each test.
"""

import os
from collections.abc import AsyncIterator

# Must be set before any `app.*` import, because settings are loaded at import time.
os.environ["APP_ENV"] = "test"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.main import create_app
from app.models import Base

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    if TEST_DATABASE_URL.startswith("sqlite"):
        engine = create_async_engine(
            TEST_DATABASE_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    else:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession], redis: fakeredis.FakeAsyncRedis
) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_redis] = lambda: redis

    # ASGITransport does not run the lifespan, so no real DB/Redis connections are attempted.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
