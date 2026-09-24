from typing import Any

import fakeredis
import pytest
from httpx import AsyncClient

from app.services.embedding import openai_embedder
from app.services.recommendation.cache import CACHE_TTL_SECONDS, RecommendationCache
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeVectorStore

BY_TEXT = "/api/v1/recommend/by-text"
DISHES: list[dict[str, Any]] = [
    {
        "external_id": "dish-1",
        "name": "Arrabbiata",
        "description": "Spicy tomato pasta",
        "cuisine": "Italian",
        "price_range": "$$",
    },
    {
        "external_id": "dish-2",
        "name": "Paneer Tikka",
        "description": "Smoky grilled paneer",
        "cuisine": "Indian",
        "price_range": "$",
    },
]


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


@pytest.fixture
async def food(client: AsyncClient) -> TenantAuth:
    auth = await register_tenant(client, "food@acme.example", "FOOD")
    await client.post(
        "/api/v1/items/upload", json={"items": DISHES, "async": False}, headers=auth.headers
    )
    return auth


async def query(client: AsyncClient, auth: TenantAuth, **body: Any) -> Any:
    return await client.post(
        BY_TEXT, json={"query": "spicy veg dish", **body}, headers=auth.headers
    )


async def test_second_identical_query_is_a_hit(
    client: AsyncClient, food: TenantAuth, vector_store: FakeVectorStore
) -> None:
    first = await query(client, food)
    second = await query(client, food)

    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert second.json()["results"] == first.json()["results"]
    assert len(vector_store.queries) == 1  # the hit never reached Pinecone
    # Each request still gets its own query_id for feedback.
    assert second.json()["query_id"] != first.json()["query_id"]


async def test_different_filters_or_top_k_miss(client: AsyncClient, food: TenantAuth) -> None:
    await query(client, food)

    other_filter = await query(client, food, filters={"cuisine": "Indian"})
    other_top_k = await query(client, food, top_k=1)

    assert other_filter.headers["X-Cache"] == "MISS"
    assert other_top_k.headers["X-Cache"] == "MISS"


async def test_equivalent_filter_spellings_share_an_entry(
    client: AsyncClient, food: TenantAuth
) -> None:
    await query(client, food, filters={"cuisine": "Indian"})
    same = await query(client, food, filters={"cuisine": {"eq": "Indian"}})
    assert same.headers["X-Cache"] == "HIT"


async def test_raw_data_requests_bypass_the_cache(
    client: AsyncClient, food: TenantAuth, vector_store: FakeVectorStore
) -> None:
    first = await query(client, food, include_raw_data=True)
    second = await query(client, food, include_raw_data=True)

    assert first.headers["X-Cache"] == second.headers["X-Cache"] == "BYPASS"
    assert len(vector_store.queries) == 2


async def test_cache_is_per_tenant(client: AsyncClient, food: TenantAuth) -> None:
    other = await register_tenant(client, "food2@acme.example", "FOOD")
    await query(client, food)
    assert (await query(client, other)).headers["X-Cache"] == "MISS"


async def test_entries_expire_after_five_minutes(
    client: AsyncClient, food: TenantAuth, redis: fakeredis.FakeAsyncRedis
) -> None:
    await query(client, food)

    [key] = [k async for k in redis.scan_iter("rec:*")]
    assert key.startswith("rec:")
    assert 0 < await redis.ttl(key) <= CACHE_TTL_SECONDS == 300


async def test_by_item_and_profile_are_cached_too(client: AsyncClient, food: TenantAuth) -> None:
    by_item = {"external_id": "dish-1"}
    profile = {"profile": {"cuisine": "Italian"}}
    for path, body in (("by-item", by_item), ("by-profile", profile)):
        url = f"/api/v1/recommend/{path}"
        first = await client.post(url, json=body, headers=food.headers)
        second = await client.post(url, json=body, headers=food.headers)
        assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("MISS", "HIT")


async def test_batch_reports_partial_hits(client: AsyncClient, food: TenantAuth) -> None:
    await query(client, food)  # caches "spicy veg dish"

    response = await client.post(
        "/api/v1/recommend/batch",
        json={"queries": [{"id": "a", "query": "spicy veg dish"}, {"id": "b", "query": "dessert"}]},
        headers=food.headers,
    )

    assert response.headers["X-Cache"] == "PARTIAL"


async def test_broken_redis_degrades_to_a_miss() -> None:
    class BrokenRedis:
        async def get(self, key: str) -> None:
            raise ConnectionError("redis down")

        async def set(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("redis down")

    cache = RecommendationCache(BrokenRedis())  # type: ignore[arg-type]
    assert await cache.get("rec:x") is None
    await cache.set("rec:x", [])  # does not raise
