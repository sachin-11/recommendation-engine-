from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services.embedding import openai_embedder
from tests.conftest import TenantAuth, register_tenant
from tests.fakes import FakeOpenAIClient, FakeVectorStore

ITEMS = "/api/v1/items"

JOBS: list[dict[str, Any]] = [
    {
        "external_id": "job-1",
        "title": "Backend Engineer",
        "description": "Build Python APIs with FastAPI",
        "skills": ["Python", "FastAPI"],
        "location": "Bangalore",
    },
    {"external_id": "job-2", "title": "Data Scientist", "description": "Train ranking models"},
    {"external_id": "job-3", "title": "ML Engineer", "description": "Ship embeddings"},
]


@pytest.fixture(autouse=True)
def single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_embedder, "RETRY_ATTEMPTS", 1)


async def upload(
    client: AsyncClient, auth: TenantAuth, items: list[dict[str, Any]], *, run_async: bool
) -> Any:
    return await client.post(
        f"{ITEMS}/upload", json={"items": items, "async": run_async}, headers=auth.headers
    )


# --- JSON upload ---


async def test_sync_upload_embeds_before_responding(
    client: AsyncClient, hr_tenant: TenantAuth, vector_store: FakeVectorStore
) -> None:
    response = await upload(client, hr_tenant, JOBS, run_async=False)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "sync"
    assert (body["total_items"], body["succeeded"], body["failed"]) == (3, 3, 0)
    assert [r["status"] for r in body["results"]] == ["DONE"] * 3
    assert len(vector_store.vectors(hr_tenant.tenant_id)) == 3


async def test_async_upload_returns_batch_that_completes(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    response = await upload(client, hr_tenant, JOBS, run_async=True)

    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["mode"] == "async"
    assert queued["total_items"] == 3
    assert queued["status_url"] == f"{ITEMS}/batch/{queued['batch_id']}"

    # ASGITransport runs the background task before returning, so it has finished.
    status = await client.get(queued["status_url"], headers=hr_tenant.headers)
    assert status.status_code == 200
    batch = status.json()
    assert batch["status"] == "DONE"
    assert batch["processed_items"] == 3
    assert batch["failed_items"] == 0
    assert batch["progress_percentage"] == 100.0
    assert batch["completed_at"] is not None


async def test_food_items_use_food_domain_config(
    client: AsyncClient, food_tenant: TenantAuth, vector_store: FakeVectorStore
) -> None:
    dishes = [
        {
            "external_id": "dish-1",
            "name": "Masala Dosa",
            "description": "Crispy rice crepe with potato filling",
            "cuisine": "South Indian",
            "price_range": "$",
        }
    ]

    response = await upload(client, food_tenant, dishes, run_async=False)

    assert response.json()["succeeded"] == 1
    [vector] = vector_store.vectors(food_tenant.tenant_id).values()
    assert vector["metadata"]["cuisine"] == "South Indian"
    assert vector["metadata"]["price_range"] == "$"


async def test_reupload_updates_instead_of_duplicating(
    client: AsyncClient, hr_tenant: TenantAuth, vector_store: FakeVectorStore
) -> None:
    await upload(client, hr_tenant, JOBS, run_async=False)
    changed = {**JOBS[0], "title": "Staff Backend Engineer"}

    await upload(client, hr_tenant, [changed], run_async=False)

    listing = (await client.get(ITEMS, headers=hr_tenant.headers)).json()
    assert listing["total"] == 3
    job1 = next(i for i in listing["items"] if i["external_id"] == "job-1")
    assert job1["raw_data"]["title"] == "Staff Backend Engineer"
    assert len(vector_store.vectors(hr_tenant.tenant_id)) == 3


async def test_duplicate_external_ids_in_one_request_keep_the_last(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    items = [{**JOBS[0], "title": "first"}, {**JOBS[0], "title": "second"}]

    response = await upload(client, hr_tenant, items, run_async=True)

    assert response.json()["total_items"] == 1
    listing = (await client.get(ITEMS, headers=hr_tenant.headers)).json()
    assert [i["raw_data"]["title"] for i in listing["items"]] == ["second"]


async def test_sync_upload_over_limit_is_rejected(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    items = [
        {"external_id": f"j{i}", "description": "x"} for i in range(settings.MAX_SYNC_ITEMS + 1)
    ]

    response = await upload(client, hr_tenant, items, run_async=False)

    assert response.status_code == 400
    assert "async=true" in response.json()["error"]["message"]


async def test_more_than_max_items_is_rejected(
    client: AsyncClient, hr_tenant: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_REQUEST", 2)

    response = await upload(client, hr_tenant, JOBS, run_async=True)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_items_need_an_external_id(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    response = await upload(client, hr_tenant, [{"description": "no id"}], run_async=True)
    assert response.status_code == 422


async def test_openai_down_returns_503_and_keeps_items_pending(
    client: AsyncClient, hr_tenant: TenantAuth, openai_client: FakeOpenAIClient
) -> None:
    openai_client.embeddings.down = True

    response = await upload(client, hr_tenant, JOBS, run_async=False)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    pending = (
        await client.get(ITEMS, params={"status": "PENDING"}, headers=hr_tenant.headers)
    ).json()
    assert pending["total"] == 3


# --- CSV upload ---


async def test_csv_upload_maps_columns(
    client: AsyncClient, hr_tenant: TenantAuth, vector_store: FakeVectorStore
) -> None:
    csv_body = (
        "ID,Job Title,Description,Skills,Location,Salary\n"
        "c-1,Backend Engineer,Build APIs,Python,Pune,100\n"
        "c-2,Designer,Design screens,Figma,Remote,\n"
        ",,,,,\n"
    )

    response = await client.post(
        f"{ITEMS}/upload-csv",
        files={"file": ("jobs.csv", csv_body, "text/csv")},
        headers=hr_tenant.headers,
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["total_items"] == 2
    assert body["column_mapping"] == {
        "ID": "external_id",
        "Job Title": "job_title",
        "Description": "description",
        "Skills": "skills",
        "Location": "location",
        "Salary": "salary",
    }
    batch = (await client.get(body["status_url"], headers=hr_tenant.headers)).json()
    assert batch["status"] == "DONE"
    assert len(vector_store.vectors(hr_tenant.tenant_id)) == 2


async def test_csv_with_wrong_columns_returns_400_with_suggestions(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    csv_body = "sku,title,descriptn\n1,Engineer,Build things\n"

    response = await client.post(
        f"{ITEMS}/upload-csv",
        files={"file": ("jobs.csv", csv_body, "text/csv")},
        headers=hr_tenant.headers,
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "bad_request"
    missing = next(d for d in error["details"] if d["field"] == "description")
    assert "'descriptn'" in missing["message"]


async def test_csv_rows_without_id_are_rejected(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    csv_body = "external_id,description\n,no id here\n"

    response = await client.post(
        f"{ITEMS}/upload-csv",
        files={"file": ("jobs.csv", csv_body, "text/csv")},
        headers=hr_tenant.headers,
    )

    assert response.status_code == 400
    assert "no external_id" in response.json()["error"]["message"]


# --- Listing, deletion, isolation ---


async def test_list_items_paginates_and_filters(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    items = [{"external_id": f"j{i}", "description": f"job {i}"} for i in range(25)]
    items.append({"external_id": "bad", "location": "nowhere"})  # nothing to embed -> FAILED
    await upload(client, hr_tenant, items, run_async=True)

    page1 = (await client.get(ITEMS, headers=hr_tenant.headers)).json()
    page2 = (await client.get(ITEMS, params={"page": 2}, headers=hr_tenant.headers)).json()
    failed = (
        await client.get(ITEMS, params={"status": "FAILED"}, headers=hr_tenant.headers)
    ).json()

    assert (page1["total"], page1["pages"], page1["page_size"], len(page1["items"])) == (
        26,
        2,
        20,
        20,
    )
    assert len(page2["items"]) == 6
    assert [i["external_id"] for i in failed["items"]] == ["bad"]
    assert "No embeddable text" in failed["items"][0]["metadata"]["error"]


async def test_delete_removes_item_and_vector(
    client: AsyncClient, hr_tenant: TenantAuth, vector_store: FakeVectorStore
) -> None:
    await upload(client, hr_tenant, JOBS, run_async=False)

    response = await client.delete(f"{ITEMS}/job-1", headers=hr_tenant.headers)

    assert response.status_code == 204
    assert len(vector_store.vectors(hr_tenant.tenant_id)) == 2
    assert (await client.get(ITEMS, headers=hr_tenant.headers)).json()["total"] == 2
    again = await client.delete(f"{ITEMS}/job-1", headers=hr_tenant.headers)
    assert again.status_code == 404


async def test_delete_keeps_item_when_pinecone_is_down(
    client: AsyncClient, hr_tenant: TenantAuth, vector_store: FakeVectorStore
) -> None:
    await upload(client, hr_tenant, JOBS[:1], run_async=False)
    vector_store.unavailable = True

    response = await client.delete(f"{ITEMS}/job-1", headers=hr_tenant.headers)

    assert response.status_code == 503
    assert (await client.get(ITEMS, headers=hr_tenant.headers)).json()["total"] == 1


async def test_tenants_cannot_see_each_others_data(
    client: AsyncClient, hr_tenant: TenantAuth, food_tenant: TenantAuth
) -> None:
    queued = (await upload(client, hr_tenant, JOBS, run_async=True)).json()

    batch = await client.get(queued["status_url"], headers=food_tenant.headers)
    listing = await client.get(ITEMS, headers=food_tenant.headers)
    delete = await client.delete(f"{ITEMS}/job-1", headers=food_tenant.headers)

    assert batch.status_code == 404
    assert listing.json()["total"] == 0
    assert delete.status_code == 404


# --- Index ---


async def test_index_stats(client: AsyncClient, hr_tenant: TenantAuth) -> None:
    empty = (await client.get("/api/v1/index/stats", headers=hr_tenant.headers)).json()
    assert empty["exists"] is False and empty["total_vector_count"] == 0

    await upload(client, hr_tenant, JOBS, run_async=False)
    stats = (await client.get("/api/v1/index/stats", headers=hr_tenant.headers)).json()

    assert stats["index_name"] == f"reco-{hr_tenant.tenant_id[:8]}"
    assert stats["exists"] is True
    assert stats["total_vector_count"] == 3
    assert stats["items_by_status"] == {"PENDING": 0, "PROCESSING": 0, "DONE": 3, "FAILED": 0}


async def test_rebuild_reembeds_all_items(
    client: AsyncClient, hr_tenant: TenantAuth, openai_client: FakeOpenAIClient, redis: Any
) -> None:
    await upload(client, hr_tenant, JOBS, run_async=False)
    await redis.flushall()  # drop the embedding cache so the rebuild calls OpenAI again
    calls_before = len(openai_client.embeddings.calls)

    response = await client.post("/api/v1/index/rebuild", headers=hr_tenant.headers)

    assert response.status_code == 202
    body = response.json()
    assert body["total_items"] == 3
    batch = (await client.get(body["status_url"], headers=hr_tenant.headers)).json()
    assert batch["status"] == "DONE" and batch["processed_items"] == 3
    assert len(openai_client.embeddings.calls) == calls_before + 1


async def test_rebuild_with_no_items_is_immediately_done(
    client: AsyncClient, hr_tenant: TenantAuth
) -> None:
    response = await client.post("/api/v1/index/rebuild", headers=hr_tenant.headers)

    assert response.status_code == 202
    assert response.json()["status"] == "DONE"
    assert response.json()["total_items"] == 0


# --- Rate limits ---


async def test_requests_per_minute_limit_returns_429(
    client: AsyncClient, hr_tenant: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_RPM", 2)

    codes = [(await client.get(ITEMS, headers=hr_tenant.headers)).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    limited = await client.get(ITEMS, headers=hr_tenant.headers)
    assert limited.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60


async def test_daily_item_limit_returns_429_without_consuming_quota(
    client: AsyncClient, hr_tenant: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DAILY_ITEM_LIMIT", 4)
    await upload(client, hr_tenant, JOBS, run_async=True)  # 3 of 4 used

    too_many = await upload(client, hr_tenant, JOBS[:2], run_async=True)
    just_enough = await upload(client, hr_tenant, JOBS[:1], run_async=True)

    assert too_many.status_code == 429
    assert "Retry-After" in too_many.headers
    assert just_enough.status_code == 202


async def test_limits_are_per_tenant(
    client: AsyncClient, hr_tenant: TenantAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DAILY_ITEM_LIMIT", 3)
    other = await register_tenant(client, "other@acme.example")

    first = await upload(client, hr_tenant, JOBS, run_async=True)
    second = await upload(client, other, JOBS, run_async=True)

    assert (first.status_code, second.status_code) == (202, 202)
