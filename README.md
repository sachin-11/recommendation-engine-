# Recommendation Engine

A multi-tenant, domain-agnostic recommendation engine SaaS. Each tenant describes its own item schema (jobs, dishes, products, courses, or anything custom), and the engine recommends items using OpenAI embeddings and Pinecone vector search.

> **Status:** Module 1 (tenants, API keys) and Module 2 (item ingestion, embeddings, Pinecone) are implemented. The recommendation query API comes next (Module 3).

## Tech stack

- **API:** FastAPI, Pydantic v2
- **Database:** PostgreSQL 15 (async SQLAlchemy 2 + asyncpg), migrations with Alembic
- **Cache / rate limiting:** Redis 7
- **ML:** OpenAI embeddings (`text-embedding-3-small`), Pinecone vector index
- **Tooling:** Poetry, pytest, Ruff, Docker Compose

## Project structure

```
app/
  api/v1/               # v1 HTTP routes: tenants (router.py), items + index (items.py)
  core/                 # config, database, redis, security, exceptions
  middleware/           # X-API-Key auth and Redis rate limiting
  models/               # SQLAlchemy models (Tenant, ApiKey, Item, ItemBatch)
  schemas/              # Pydantic request/response models
  services/
    embedding/          # TextBuilder -> OpenAIEmbedder -> PineconeService, and the pipeline
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
