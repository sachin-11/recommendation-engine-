import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import pytest

from recoengine import (
    APIConnectionError,
    AsyncRecoEngineClient,
    AuthError,
    BatchResult,
    NotFoundError,
    RateLimitError,
    RecoEngineClient,
    RecoEngineError,
    ServiceUnavailableError,
    SyncUploadResult,
    ValidationError,
)

RESULTS = {
    "results": [
        {
            "rank": 1,
            "external_id": "job-101",
            "score": 0.81,
            "score_label": "Good Match",
            "metadata": {"location": "Remote"},
        }
    ],
    "total": 1,
    "query_id": "54c4f208-3af7-4790-acdb-1b246e04a27f",
    "latency_ms": 120,
    "request_id": "rid-1",
}
BATCH = {
    "batch_id": "b1",
    "status": "PENDING",
    "total_items": 1,
    "processed_items": 0,
    "failed_items": 0,
    "progress_percentage": 0.0,
    "created_at": "2026-09-24T04:16:16Z",
    "completed_at": None,
}

Handler = Callable[[httpx.Request], httpx.Response]


def error(
    status: int, code: str, message: str, headers: Optional[Dict[str, str]] = None
) -> httpx.Response:
    body = {
        "error": {"code": code, "message": message, "details": [{"field": "x", "message": "bad"}]}
    }
    return httpx.Response(status, json=body, headers=headers)


class Recorder:
    """A MockTransport handler that serves queued responses and records requests."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


def make_client(handler: Handler, **kwargs: Any) -> Tuple[RecoEngineClient, List[float]]:
    sleeps: List[float] = []
    client = RecoEngineClient(
        api_key="reco_test_key",
        base_url="http://api.test/",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        **kwargs,
    )
    return client, sleeps


# ---------------------------------------------------------------- configuration


def test_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECOENGINE_API_KEY", raising=False)
    with pytest.raises(RecoEngineError, match="api_key is required"):
        RecoEngineClient()


def test_reads_key_and_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOENGINE_API_KEY", "reco_from_env")
    monkeypatch.setenv("RECOENGINE_BASE_URL", "http://env.test")
    recorder = Recorder(httpx.Response(200, json={"deleted": 0, "not_found": []}))
    client = RecoEngineClient(transport=httpx.MockTransport(recorder))
    client.items.delete_many(["a"])
    request = recorder.requests[0]
    assert str(request.url) == "http://env.test/api/v1/items/bulk-delete"
    assert request.headers["X-API-Key"] == "reco_from_env"
    assert request.headers["User-Agent"].startswith("recoengine-python/")


# ---------------------------------------------------------------- items


def test_upload_async_returns_batch() -> None:
    recorder = Recorder(httpx.Response(202, json={**BATCH, "mode": "async", "status_url": "/x"}))
    client, _ = make_client(recorder)
    result = client.items.upload([{"external_id": "job_1", "title": "Python Dev"}])
    assert isinstance(result, BatchResult)
    assert recorder.body() == {
        "items": [{"external_id": "job_1", "title": "Python Dev"}],
        "async": True,
    }


def test_upload_sync_returns_per_item_results() -> None:
    sync = {
        "mode": "sync",
        "total_items": 1,
        "succeeded": 1,
        "failed": 0,
        "results": [{"external_id": "job_1", "status": "DONE", "error": None}],
    }
    client, _ = make_client(Recorder(httpx.Response(200, json=sync)))
    result = client.items.upload([{"external_id": "job_1"}], async_=False)
    assert isinstance(result, SyncUploadResult)
    assert result.results[0].status == "DONE"


def test_upload_csv_from_path(tmp_path: Path) -> None:
    path = tmp_path / "jobs.csv"
    path.write_text("id,description\nj1,Python role\n")
    batch = {**BATCH, "mode": "async", "status_url": "/x", "column_mapping": {"id": "external_id"}}
    recorder = Recorder(httpx.Response(202, json=batch))
    client, _ = make_client(recorder)
    result = client.items.upload_csv(path)
    assert result.column_mapping == {"id": "external_id"}
    content = recorder.requests[0].content
    assert b'filename="jobs.csv"' in content and b"Python role" in content


def test_list_and_encoded_paths() -> None:
    page = {"items": [], "total": 0, "page": 2, "page_size": 20, "pages": 0}
    recorder = Recorder(httpx.Response(200, json=page))
    client, _ = make_client(recorder)
    client.items.list(page=2, status="FAILED", search="job")
    assert recorder.requests[0].url.params == httpx.QueryParams(
        {"page": "2", "status": "FAILED", "search": "job"}
    )
    recorder.responses = [httpx.Response(204)]
    client.items.delete("a/b")
    assert recorder.requests[1].url.raw_path == b"/api/v1/items/a%2Fb"


def test_wait_for_batch() -> None:
    recorder = Recorder(
        httpx.Response(200, json={**BATCH, "status": "PROCESSING"}),
        httpx.Response(200, json={**BATCH, "status": "DONE", "processed_items": 1}),
    )
    client, _ = make_client(recorder)
    batch = client.items.wait_for_batch("b1", interval=0.01)
    assert batch.status == "DONE" and batch.is_complete


# ---------------------------------------------------------------- recommendations


def test_by_text_body_and_cache_header() -> None:
    recorder = Recorder(httpx.Response(200, json=RESULTS, headers={"X-Cache": "HIT"}))
    client, _ = make_client(recorder)
    results = client.recommend.by_text(
        query="senior python developer", top_k=10, filters={"location": "Delhi"}
    )
    assert recorder.body() == {
        "query": "senior python developer",
        "top_k": 10,
        "filters": {"location": "Delhi"},
        "include_raw_data": False,
    }
    assert results.cache == "HIT"
    assert [(r.rank, r.external_id, r.score_label) for r in results.results] == [
        (1, "job-101", "Good Match")
    ]


def test_by_item_by_profile_batch_and_feedback() -> None:
    recorder = Recorder(httpx.Response(200, json=RESULTS))
    client, _ = make_client(recorder)
    client.recommend.by_item("job-7", top_k=3)
    assert recorder.body()["external_id"] == "job-7"
    client.recommend.by_profile({"skills": "Python"}, include_raw_data=True)
    assert recorder.body()["profile"] == {"skills": "Python"}
    recorder.responses = [
        httpx.Response(
            200,
            json={
                "results": {"q1": []},
                "query_ids": {"q1": "x"},
                "latency_ms": 1,
                "request_id": "r",
            },
        )
    ]
    batch = client.recommend.batch([{"id": "q1", "query": "python"}], top_k=5)
    assert batch.query_ids == {"q1": "x"}
    recorder.responses = [httpx.Response(201, json={})]
    client.recommend.submit_feedback(RESULTS["query_id"], "job-101", "CLICK")
    assert recorder.body() == {
        "query_id": RESULTS["query_id"],
        "external_item_id": "job-101",
        "feedback_type": "CLICK",
    }


# ---------------------------------------------------------------- retries and errors


def test_retries_503_with_backoff_then_succeeds() -> None:
    recorder = Recorder(
        error(503, "service_unavailable", "down"),
        error(503, "service_unavailable", "down"),
        httpx.Response(200, json=RESULTS),
    )
    client, sleeps = make_client(recorder, retry_base_delay=0.1)
    assert client.recommend.by_text("q").total == 1
    assert len(sleeps) == 2
    assert 0.05 <= sleeps[0] <= 0.1
    assert 0.1 <= sleeps[1] <= 0.2


def test_honours_retry_after_capped() -> None:
    recorder = Recorder(
        error(429, "rate_limited", "slow", {"Retry-After": "2"}),
        error(429, "rate_limited", "slow", {"Retry-After": "3600"}),
        httpx.Response(200, json=RESULTS),
    )
    client, sleeps = make_client(recorder, max_retry_delay=5)
    client.recommend.by_text("q")
    assert sleeps == [2.0, 5]


def test_gives_up_after_three_retries() -> None:
    recorder = Recorder(
        error(
            429,
            "rate_limited",
            "Rate limit exceeded",
            {"Retry-After": "7", "X-Request-ID": "rid-429"},
        )
    )
    client, sleeps = make_client(recorder)
    with pytest.raises(RateLimitError) as info:
        client.analytics.overview()
    assert info.value.retry_after == 7
    assert info.value.request_id == "rid-429"
    assert info.value.status_code == 429
    assert len(sleeps) == 3 and len(recorder.requests) == 4


@pytest.mark.parametrize(
    ("status", "error_class"),
    [
        (401, AuthError),
        (403, AuthError),
        (404, NotFoundError),
        (400, ValidationError),
        (409, ValidationError),
        (422, ValidationError),
        (500, RecoEngineError),
    ],
)
def test_error_mapping_without_retry(status: int, error_class: type) -> None:
    client, sleeps = make_client(Recorder(error(status, "some_code", "Nope")))
    with pytest.raises(error_class) as info:
        client.items.get("x")
    assert info.value.message == "Nope"
    assert info.value.details == [{"field": "x", "message": "bad"}]
    assert sleeps == []


def test_service_unavailable_after_retries() -> None:
    client, _ = make_client(Recorder(error(503, "service_unavailable", "down")), max_retries=1)
    with pytest.raises(ServiceUnavailableError):
        client.recommend.by_text("q")


def test_connection_errors() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, _ = make_client(fail)
    with pytest.raises(APIConnectionError):
        client.analytics.overview()


def test_token_usage() -> None:
    body = {
        "days": 7,
        "since": "2026-09-18T00:00:00Z",
        "model": "text-embedding-3-small",
        "total_tokens": 1234,
        "by_source": {"INGEST": 1000, "QUERY": 234},
        "api_calls": 5,
        "texts_embedded": 12,
        "cache_hits": 3,
        "price_per_million_tokens": 0.02,
        "estimated_cost_usd": 0.000025,
        "daily": [{"date": "2026-09-24", "ingest_tokens": 1000, "query_tokens": 234}],
    }
    recorder = Recorder(httpx.Response(200, json=body))
    client, _ = make_client(recorder)
    usage = client.analytics.tokens(days=7)
    assert recorder.requests[0].url.path == "/api/v1/analytics/tokens"
    assert recorder.requests[0].url.params["days"] == "7"
    assert usage.total_tokens == 1234 and usage.by_source["QUERY"] == 234
    assert usage.daily[0].ingest_tokens == 1000


# ---------------------------------------------------------------- async client


def test_async_client() -> None:
    async def run() -> None:
        sleeps: List[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        recorder = Recorder(
            error(503, "service_unavailable", "down"),
            httpx.Response(200, json=RESULTS, headers={"X-Cache": "MISS"}),
        )
        async with AsyncRecoEngineClient(
            api_key="reco_test_key",
            base_url="http://api.test",
            transport=httpx.MockTransport(recorder),
            sleep=sleep,
        ) as client:
            results = await client.recommend.by_text("q", top_k=3)
            assert results.cache == "MISS" and len(sleeps) == 1
            recorder.responses = [error(404, "not_found", "Item 'x' not found")]
            with pytest.raises(NotFoundError):
                await client.recommend.by_item("x")

    asyncio.run(run())
