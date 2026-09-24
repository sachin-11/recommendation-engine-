from typing import Any

import pytest
from httpx import AsyncClient

from app.api.openapi import _OPERATIONS, TAGS
from app.services.embedding import openai_embedder
from tests.conftest import TenantAuth


@pytest.fixture
async def schema(client: AsyncClient) -> dict[str, Any]:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [(m, p, op) for p, ops in schema["paths"].items() for m, op in ops.items()]


async def test_every_operation_is_documented(schema: dict[str, Any]) -> None:
    published_tags = {tag["name"] for tag in TAGS}
    ids = set()
    for method, path, op in operations(schema):
        assert (method, path) in _OPERATIONS, f"{method} {path} has no entry in _OPERATIONS"
        assert op.get("description"), f"{method} {path} has no description"
        assert set(op["tags"]) <= published_tags, op["tags"]
        ids.add(op["operationId"])
    assert len(ids) == len(operations(schema)), "operationIds must be unique"


async def test_metrics_endpoint_is_not_in_the_schema(schema: dict[str, Any]) -> None:
    assert "/metrics" not in schema["paths"]


async def test_examples_are_attached(schema: dict[str, Any]) -> None:
    upload = schema["paths"]["/api/v1/items/upload"]["post"]
    examples = upload["requestBody"]["content"]["application/json"]["examples"]
    assert {"hr", "food"} <= set(examples)
    assert examples["hr"]["value"]["items"][0]["external_id"] == "job-101"
    by_profile = schema["paths"]["/api/v1/recommend/by-profile"]["post"]
    assert "candidate" in by_profile["requestBody"]["content"]["application/json"]["examples"]
    by_text_200 = schema["paths"]["/api/v1/recommend/by-text"]["post"]["responses"]["200"]
    assert by_text_200["content"]["application/json"]["example"]["results"][0]["rank"] == 1


async def test_api_key_security_scheme(schema: dict[str, Any]) -> None:
    scheme = schema["components"]["securitySchemes"]["ApiKeyAuth"]
    assert scheme == {**scheme, "type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert schema["paths"]["/api/v1/items"]["get"]["security"] == [{"ApiKeyAuth": []}]


async def test_metrics(
    client: AsyncClient, hr_tenant: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)
    await client.post(
        "/api/v1/items/upload",
        json={"async": False, "items": [{"external_id": "j1", "description": "Python role"}]},
        headers=hr_tenant.headers,
    )
    await client.post(
        "/api/v1/recommend/by-text", json={"query": "python"}, headers=hr_tenant.headers
    )

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert f'items_total{{status="DONE",tenant_id="{hr_tenant.tenant_id}"}} 1.0' in body
    assert f'reco_requests_total{{query_type="TEXT",tenant_id="{hr_tenant.tenant_id}"}} 1.0' in body
    assert 'reco_latency_seconds_bucket{le="10.0",query_type="TEXT"}' in body
    assert 'embedding_pipeline_duration_seconds_count{outcome="ok"}' in body
