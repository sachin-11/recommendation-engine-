"""Test fixtures.

Tests run against an in-memory SQLite DB and fakeredis by default, so they need no
running services. Set TEST_DATABASE_URL (postgresql+asyncpg://...) to run them
against a real Postgres instead. Note: tables are dropped after each test.
"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

# Must be set before any `app.*` import, because settings are loaded at import time.
os.environ["APP_ENV"] = "test"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-0123456789")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from app.core.database import get_db, get_session_factory
from app.core.redis_client import get_redis
from app.main import create_app
from app.models import Base
from app.services.embedding.dependencies import get_embedder, get_vector_store
from app.services.embedding.openai_embedder import OpenAIEmbedder
from app.services.embedding.pipeline import EmbeddingPipeline
from app.services.recommendation.dependencies import get_query_embedder
from tests.fakes import TEST_DIMENSION, FakeOpenAIClient, FakeVectorStore

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
def openai_client() -> FakeOpenAIClient:
    return FakeOpenAIClient()


@pytest.fixture
def embedder(openai_client: FakeOpenAIClient, redis: fakeredis.FakeAsyncRedis) -> OpenAIEmbedder:
    return OpenAIEmbedder(openai_client, redis, dimension=TEST_DIMENSION)  # type: ignore[arg-type]


@pytest.fixture
def vector_store() -> FakeVectorStore:
    return FakeVectorStore()


@pytest.fixture
def pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: OpenAIEmbedder,
    vector_store: FakeVectorStore,
) -> EmbeddingPipeline:
    return EmbeddingPipeline(session_factory, embedder, vector_store)  # type: ignore[arg-type]


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    redis: fakeredis.FakeAsyncRedis,
    embedder: OpenAIEmbedder,
    vector_store: FakeVectorStore,
) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_query_embedder] = lambda: embedder
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    # ASGITransport does not run the lifespan, so no real DB/Redis connections are attempted.
    # It also awaits background tasks before returning, so async batches finish in-request.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


# --- Tenant + API key helpers ---

HR_CONFIG: dict[str, Any] = {
    "primary_embedding_field": "description",
    "searchable_fields": ["title", "description", "skills"],
    "filter_fields": ["location", "employment_type"],
    "item_label": "job",
}


@dataclass
class TenantAuth:
    tenant_id: str
    api_key: str
    domain_config: dict[str, Any]

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}


async def register_tenant(
    client: AsyncClient,
    email: str,
    domain_type: str = "HR",
    domain_config: dict[str, Any] | None = None,
) -> TenantAuth:
    payload: dict[str, Any] = {
        "name": email.split("@")[0],
        "email": email,
        "domain_type": domain_type,
    }
    if domain_config is not None:
        payload["domain_config"] = domain_config
    tenant = await client.post("/api/v1/tenants", json=payload)
    assert tenant.status_code == 201, tenant.text
    body = tenant.json()
    key = await client.post(f"/api/v1/tenants/{body['id']}/api-keys", json={"name": "test"})
    assert key.status_code == 201, key.text
    return TenantAuth(body["id"], key.json()["api_key"], body["domain_config"])


@pytest.fixture
async def hr_tenant(client: AsyncClient) -> TenantAuth:
    return await register_tenant(client, "hr@acme.example", "HR", HR_CONFIG)


@pytest.fixture
async def food_tenant(client: AsyncClient) -> TenantAuth:
    return await register_tenant(client, "food@acme.example", "FOOD")
