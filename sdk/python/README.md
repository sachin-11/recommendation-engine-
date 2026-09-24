# recoengine

Official Python client for [RecoEngine](https://docs.recoengine.io): upload items, get recommendations by text, similar item or profile, and send feedback.

- Sync (`RecoEngineClient`) and async (`AsyncRecoEngineClient`) clients on httpx
- Pydantic v2 models for every response, type hints everywhere (`py.typed`)
- Retries `429` and `503` up to 3 times, honouring `Retry-After`, with exponential backoff
- Typed errors: `AuthError`, `NotFoundError`, `ValidationError`, `RateLimitError`, `ServiceUnavailableError`, `APIConnectionError`
- Python 3.9+

## Install

```bash
pip install recoengine
```

## Usage

```python
from recoengine import RecoEngineClient

client = RecoEngineClient(api_key="reco_xxxx")  # or set RECOENGINE_API_KEY

# Upload items (queued; wait for embedding)
batch = client.items.upload([
    {"external_id": "job_1", "title": "Python Dev", "description": "Build FastAPI services"},
])
client.items.wait_for_batch(batch.batch_id)

# Get recommendations
results = client.recommend.by_text(
    query="senior python developer",
    top_k=10,
    filters={"location": "Delhi"},
)
for item in results.results:
    print(f"{item.rank}. {item.external_id} — {item.score_label}")

# Tell us what the user did
client.recommend.submit_feedback(results.query_id, results.results[0].external_id, "CLICK")
```

### Async

```python
import asyncio
from recoengine import AsyncRecoEngineClient

async def main() -> None:
    async with AsyncRecoEngineClient(api_key="reco_xxxx") as client:
        results = await client.recommend.by_profile(
            {"skills": "Python, FastAPI", "experience": "5 years backend"}, top_k=5
        )
        print([r.external_id for r in results.results])

asyncio.run(main())
```

## Methods

The async client has the same methods as coroutines.

| Method | Returns |
|---|---|
| `items.upload(items, async_=True)` | `BatchResult`, or `SyncUploadResult` with `async_=False` (≤ 50 items) |
| `items.upload_csv(path_or_bytes_or_file, filename=None)` | `CsvBatchResult` |
| `items.get_batch_status(batch_id)` / `items.wait_for_batch(batch_id, interval=2, timeout=300)` | `BatchStatus` |
| `items.get(external_id)` / `items.list(page=1, status=None, search=None)` | `Item` / `PaginatedItems` |
| `items.delete(external_id)` / `items.delete_many(ids)` | `None` / `DeleteResult` |
| `recommend.by_text(query, top_k=10, filters=None, include_raw_data=False)` | `RecommendResult` |
| `recommend.by_item(external_id, ...)` / `recommend.by_profile(profile, ...)` | `RecommendResult` |
| `recommend.batch(queries, top_k=10)` | `BatchRecommendResult` |
| `recommend.submit_feedback(query_id, item_id, feedback_type)` | `None` |
| `analytics.overview()` / `analytics.feedback_summary(days=30)` / `analytics.usage(days=30)` | stats models |

Filters use your domain config's `filter_fields`: `"Delhi"` exact, `["Delhi", "Pune"]` any of, `{"gte": 3, "lte": 8}` range.

## Errors and retries

```python
from recoengine import RateLimitError, ValidationError

try:
    client.recommend.by_text("…", filters={"salary": 5})
except ValidationError as e:
    print(e.message, e.details)       # unknown filter field
except RateLimitError as e:
    print(f"retry in {e.retry_after}s")
```

Every error has `status_code`, `code`, `details` and `request_id`. `429` and `503` are retried `max_retries` (default 3) times: the wait is `Retry-After` when sent, otherwise `retry_base_delay × 2^attempt` with jitter, capped at `max_retry_delay` (default 30 s).

## Configuration

```python
RecoEngineClient(
    api_key="reco_…",                        # or RECOENGINE_API_KEY
    base_url="https://api.recoengine.io",     # or RECOENGINE_BASE_URL
    timeout=10.0,
    max_retries=3,
)
```

Use it as a context manager (`with RecoEngineClient(...) as client:`) or call `client.close()`.

## Development

```bash
pip install -e ".[dev]"
pytest && ruff check . && mypy recoengine
```
