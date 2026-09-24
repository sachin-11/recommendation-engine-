# Recommendation Engine

A multi-tenant, domain-agnostic recommendation engine SaaS. Each tenant describes its own item schema (jobs, dishes, products, courses, or anything custom), and the engine recommends items using OpenAI embeddings and Pinecone vector search.

> **Status:** Module 1 (tenants, API keys), Module 2 (item ingestion, embeddings, Pinecone) and Module 3 (recommendation queries, feedback, analytics) are implemented.

## Tech stack

- **API:** FastAPI, Pydantic v2
- **Database:** PostgreSQL 15 (async SQLAlchemy 2 + asyncpg), migrations with Alembic
- **Cache / rate limiting:** Redis 7
- **ML:** OpenAI embeddings (`text-embedding-3-small`), Pinecone vector index
- **Tooling:** Poetry, pytest, Ruff, Docker Compose

## Project structure

```
app/
  api/v1/               # v1 routes: tenants, items + index, recommend, analytics
  core/                 # config, database, redis, security, exceptions, logging
  middleware/           # X-API-Key auth, Redis rate limiting, X-Request-ID
  models/               # Tenant, ApiKey, Item, ItemBatch, RecommendationLog, UserFeedback
  schemas/              # Pydantic request/response models
  services/
    embedding/          # TextBuilder -> OpenAIEmbedder -> PineconeService, and the pipeline
    recommendation/     # QueryEngine, FilterBuilder, ResultFormatter, result cache, logging
    analytics_service.py
    item_service.py     # ingestion, listing, deletion, rebuild
    csv_import.py       # CSV parsing and column auto-detection
  workers/              # standalone embedding worker
  main.py               # app factory, lifespan, error handling, /health
alembic/                # database migrations
tests/                  # pytest suite (SQLite, fakeredis, fake OpenAI/Pinecone)
```

## Getting started

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at least `POSTGRES_PASSWORD`, `DATABASE_URL` and `SECRET_KEY`. Generate a secret key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Set `OPENAI_API_KEY` and `PINECONE_API_KEY` to embed items. Without them the API still runs: uploads are stored as `PENDING` and embedded by the worker once the keys are set. Both keys are required when `APP_ENV=production`, and in production any value containing `change-me` is rejected at startup.

### 2a. Run with Docker (recommended)

```bash
docker compose up --build
```

This starts PostgreSQL, Redis, the API and the embedding worker, runs `alembic upgrade head`, and serves the app with auto-reload on http://localhost:8000.

### 2b. Run locally

Requires Python 3.11+, Poetry 2.x, and PostgreSQL + Redis running (for example `docker compose up postgres redis`).

```bash
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
poetry run python -m app.workers.embedding_worker   # in a second terminal
```

### API docs

Interactive docs are at http://localhost:8000/docs (Swagger) and `/redoc`. They are disabled when `APP_ENV=production`.

## Domains

Every tenant has a `domain_type`. Built-in presets supply a default `domain_config`:

| Domain      | Item label | Embedding field | Filter fields                              |
|-------------|------------|-----------------|--------------------------------------------|
| `HR`        | job        | description     | location, department, employment_type      |
| `FOOD`      | dish       | description     | cuisine, dietary_tags, price_range         |
| `ECOMMERCE` | product    | description     | category, brand, price_range               |
| `EDTECH`    | course     | description     | level, category, language                  |
| `CUSTOM`    | *you define* | *you define*  | *you define*                               |

`CUSTOM` tenants must provide their own `domain_config`:

```json
{
  "primary_embedding_field": "description",
  "searchable_fields": ["title", "description", "tags"],
  "filter_fields": ["location", "category"],
  "item_label": "job"
}
```

`primary_embedding_field` must be one of `searchable_fields`.

## API

| Method   | Path                                          | Description                                  |
|----------|-----------------------------------------------|----------------------------------------------|
| `GET`    | `/health`                                     | Health check for the database and Redis (returns 503 if one is down) |
| `POST`   | `/api/v1/tenants`                             | Register a tenant                            |
| `GET`    | `/api/v1/tenants/{tenant_id}`                 | Get tenant details                           |
| `POST`   | `/api/v1/tenants/{tenant_id}/api-keys`        | Create an API key (plain key returned once)  |
| `GET`    | `/api/v1/tenants/{tenant_id}/api-keys`        | List API keys (prefix only)                  |
| `DELETE` | `/api/v1/tenants/{tenant_id}/api-keys/{key_id}` | Revoke an API key                          |

These routes need an `X-API-Key` header:

| Method   | Path                                  | Description                                                  |
|----------|---------------------------------------|--------------------------------------------------------------|
| `POST`   | `/api/v1/items/upload`                | Upload up to 1000 items as JSON. `async=false` processes up to 50 before responding |
| `POST`   | `/api/v1/items/upload-csv`            | Upload a CSV file (always async)                             |
| `GET`    | `/api/v1/items/batch/{batch_id}`      | Batch status and progress                                    |
| `GET`    | `/api/v1/items?status=&page=`         | List items, 20 per page, optionally by `embedding_status`    |
| `DELETE` | `/api/v1/items/{external_id}`         | Delete an item from the database and Pinecone                |
| `GET`    | `/api/v1/index/stats`                 | Pinecone index stats and item counts by status               |
| `POST`   | `/api/v1/index/rebuild`               | Re-embed every item (for example after a domain config change) |
| `POST`   | `/api/v1/recommend/by-text`           | Items matching free text                                     |
| `POST`   | `/api/v1/recommend/by-item`           | Items similar to one of your items (never includes itself)   |
| `POST`   | `/api/v1/recommend/by-profile`        | Items matching a profile, e.g. a candidate's resume fields   |
| `POST`   | `/api/v1/recommend/batch`             | Up to 20 text queries in one request                         |
| `POST`   | `/api/v1/recommend/feedback`          | Record CLICK, THUMBS_UP, THUMBS_DOWN, PURCHASE, APPLY or IGNORE |
| `GET`    | `/api/v1/analytics/overview`          | Item count, query volume, average latency, top items         |
| `GET`    | `/api/v1/analytics/feedback-summary`  | Feedback counts by type (default: last 30 days)              |

Every response carries an `X-Request-ID` header (a well-formed incoming one is reused).

### Example

```bash
# Register a tenant
curl -X POST http://localhost:8000/api/v1/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Jobs", "email": "admin@acme.com", "domain_type": "HR"}'

# Create an API key for it
curl -X POST http://localhost:8000/api/v1/tenants/<tenant_id>/api-keys \
  -H "Content-Type: application/json" \
  -d '{"name": "production-backend"}'

# Upload items (use the api_key returned above)
curl -X POST http://localhost:8000/api/v1/items/upload \
  -H "X-API-Key: reco_..." -H "Content-Type: application/json" \
  -d '{"async": true, "items": [{"external_id": "job-1", "title": "Backend Engineer", "description": "Build Python APIs", "location": "Pune"}]}'
```

Errors use one consistent shape:

```json
{ "error": { "code": "not_found", "message": "Tenant '...' not found" } }
```

## How ingestion works

1. **Text:** `TextBuilder` joins the tenant's `primary_embedding_field` and other `searchable_fields` into one text, like `description: ... title: ... skills: ...`. This is what keeps the engine domain-agnostic.
2. **Embedding:** `OpenAIEmbedder` calls `text-embedding-3-small` (1536 dimensions). Texts are truncated to 8000 tokens, rate limits and outages are retried with exponential backoff, and embeddings are cached in Redis for 24 hours (`emb:<sha256>`).
3. **Storage:** `PineconeService` upserts vectors into the tenant's own serverless index (`reco-<first 8 chars of tenant_id>`), with metadata `external_id`, `tenant_id` and the `filter_fields`.

Item status goes `PENDING` → `PROCESSING` → `DONE` or `FAILED` (the reason is in `metadata.error`). Re-uploading an `external_id` updates the item instead of duplicating it.

If OpenAI or Pinecone is down, items go back to `PENDING` and a synchronous upload returns `503`. The worker (`python -m app.workers.embedding_worker`, a service in docker-compose) keeps polling for `PENDING` items and processes them once the service recovers. It also picks up items stuck in `PROCESSING` after a crash.

**CSV uploads:** headers are matched to domain-config fields ignoring case and punctuation (`Dietary-Tags` → `dietary_tags`). The id column can be named `external_id`, `id`, `item_id` or `sku`. A CSV without an id or primary-field column gets a `400` with suggested column names.

**Rate limits** (Redis): 100 requests per minute per API key, and 10,000 ingested items per tenant per UTC day. Both return `429` with a `Retry-After` header and are set by `RATE_LIMIT_RPM` and `DAILY_ITEM_LIMIT`.

## Recommendations

Every query returns ranked results, a `query_id` for feedback, and the latency:

```json
{
  "results": [
    {
      "rank": 1,
      "external_id": "job-101",
      "score": 0.7677,
      "score_label": "Good Match",
      "metadata": {"department": "Engineering", "employment_type": "full_time", "location": "Bangalore"}
    }
  ],
  "total": 1,
  "query_id": "54c4f208-3af7-4790-acdb-1b246e04a27f",
  "latency_ms": 712,
  "request_id": "74996b11-d452-4da6-8046-b83104c0e312"
}
```

- **Filters** use the tenant's `filter_fields`: `"Delhi"` (exact), `["Delhi", "Pune"]` (any of), `{"gte": 3, "lte": 8}` (range; also `gt`, `lt`, `eq`, `ne`, `in`, `nin`). Unknown fields or wrong value types get a `400`. Range filters only match values uploaded as JSON numbers; CSV values are stored as text.
- **`score_label`**: above 0.85 Excellent, above 0.7 Good, above 0.5 Fair, otherwise Weak. Short text queries against `text-embedding-3-small` usually score 0.3 to 0.6 even when the ranking is right, so treat the label as a rough guide.
- **`include_raw_data: true`** adds each item's full uploaded data.
- **Caching:** results are cached in Redis for 5 minutes (`rec:{tenant_id}:{sha256}`), keyed on the query, the normalised filters and `top_k`. The `X-Cache` header is `HIT`, `MISS`, or `BYPASS` (with `include_raw_data`); a batch can also be `PARTIAL`.
- **Failures:** if Pinecone does not answer within 5 seconds, or OpenAI or Pinecone is down, the API returns `503` with `Retry-After: 5`, never partial results.
- **Logging:** every query is stored in `recommendation_logs` after the response is sent, and logged with structlog (`tenant_id`, `query_type`, `latency_ms`, `result_count`, `request_id`).

**Latency.** Measured from a laptop in India against Pinecone and OpenAI in `us-east-1`, a warm by-item query takes about 320 ms and a by-text query about 700 ms. Almost all of that is network time: a single Pinecone query is about 300 ms and an OpenAI embedding about 400 ms from there, while the app adds about 20 ms. To get under 200 ms, deploy the API in the same region as the Pinecone index. Cache hits take under 10 ms.

### HR: candidate profile → matching jobs

Register the tenant with the filters you need (this overrides the HR preset):

```bash
curl -X POST http://localhost:8000/api/v1/tenants -H "Content-Type: application/json" -d '{
  "name": "Acme Hiring", "email": "hiring@acme.com", "domain_type": "HR",
  "domain_config": {
    "primary_embedding_field": "description",
    "searchable_fields": ["title", "description", "skills"],
    "filter_fields": ["location", "experience_years", "job_type"],
    "item_label": "job"
  }}'

# Jobs for a candidate: every profile field is used, whatever its name
curl -X POST http://localhost:8000/api/v1/recommend/by-profile \
  -H "X-API-Key: $HR_KEY" -H "Content-Type: application/json" -d '{
  "profile": {
    "skills": "Python, FastAPI, PostgreSQL",
    "experience": "5 years backend development",
    "preferred_location": "Remote"
  },
  "top_k": 10,
  "filters": {"location": ["Remote", "Delhi"], "experience_years": {"lte": 5}, "job_type": "full_time"}
}'

# Jobs similar to this one
curl -X POST http://localhost:8000/api/v1/recommend/by-item \
  -H "X-API-Key: $HR_KEY" -H "Content-Type: application/json" \
  -d '{"external_id": "job-101", "top_k": 5, "filters": {"location": "Delhi"}}'
```

### Food: "spicy veg dish" → dishes

```bash
curl -X POST http://localhost:8000/api/v1/tenants -H "Content-Type: application/json" -d '{
  "name": "Acme Kitchen", "email": "kitchen@acme.com", "domain_type": "FOOD",
  "domain_config": {
    "primary_embedding_field": "description",
    "searchable_fields": ["name", "description", "cuisine", "ingredients"],
    "filter_fields": ["cuisine", "is_vegetarian", "price_range"],
    "item_label": "dish"
  }}'

curl -X POST http://localhost:8000/api/v1/recommend/by-text \
  -H "X-API-Key: $FOOD_KEY" -H "Content-Type: application/json" \
  -d '{"query": "spicy vegetarian pasta", "top_k": 5, "filters": {"is_vegetarian": true, "price_range": ["$", "$$"]}}'

curl -X POST http://localhost:8000/api/v1/recommend/by-item \
  -H "X-API-Key: $FOOD_KEY" -H "Content-Type: application/json" \
  -d '{"external_id": "dish-1", "top_k": 5, "filters": {"cuisine": {"ne": "Italian"}}}'
```

### E-commerce: products

Upload `price` as a number so range filters work:

```bash
curl -X POST http://localhost:8000/api/v1/tenants -H "Content-Type: application/json" -d '{
  "name": "Acme Store", "email": "store@acme.com", "domain_type": "ECOMMERCE",
  "domain_config": {
    "primary_embedding_field": "description",
    "searchable_fields": ["title", "description", "brand", "tags"],
    "filter_fields": ["category", "brand", "price"],
    "item_label": "product"
  }}'

curl -X POST http://localhost:8000/api/v1/recommend/by-text \
  -H "X-API-Key: $STORE_KEY" -H "Content-Type: application/json" \
  -d '{"query": "wireless noise cancelling headphones", "top_k": 10, "filters": {"category": "audio", "price": {"lte": 5000}}}'

curl -X POST http://localhost:8000/api/v1/recommend/by-item \
  -H "X-API-Key: $STORE_KEY" -H "Content-Type: application/json" \
  -d '{"external_id": "sku-2231", "top_k": 8, "filters": {"brand": ["Sony", "JBL"]}}'
```

### Feedback and analytics

```bash
curl -X POST http://localhost:8000/api/v1/recommend/feedback \
  -H "X-API-Key: $HR_KEY" -H "Content-Type: application/json" \
  -d '{"query_id": "<query_id from a recommendation>", "external_item_id": "job-101", "feedback_type": "APPLY"}'

curl http://localhost:8000/api/v1/analytics/overview -H "X-API-Key: $HR_KEY"
curl "http://localhost:8000/api/v1/analytics/feedback-summary?days=30" -H "X-API-Key: $HR_KEY"
```

## Security

- API keys look like `reco_<random>` and are shown **only once**, at creation.
- Only an HMAC-SHA256 hash of each key (keyed with `SECRET_KEY`) is stored, so a leaked database cannot be used to verify keys. Changing `SECRET_KEY` invalidates every existing key.
- Keys can have an optional `expires_at`. Expired or revoked keys get `401`.
- Never commit `.env`. It is already in `.gitignore`.

## Testing and linting

```bash
poetry run pytest
poetry run ruff check .
```

Tests use SQLite, fakeredis and in-memory fakes for OpenAI and Pinecone, so no running services or API keys are needed. To run them against Postgres, set `TEST_DATABASE_URL=postgresql+asyncpg://...` (tables are dropped after each test).

## Database migrations

```bash
poetry run alembic revision --autogenerate -m "describe change"
poetry run alembic upgrade head
```
