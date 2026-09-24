# Recommendation Engine

A multi-tenant, domain-agnostic recommendation engine SaaS. Each tenant describes its own item schema (jobs, dishes, products, courses, or anything custom), and the engine recommends items using OpenAI embeddings and Pinecone vector search.

> **Status:** early development. Tenant registration and API-key management are implemented. Embedding and recommendation endpoints are planned next (Module 2).

## Tech stack

- **API:** FastAPI, Pydantic v2
- **Database:** PostgreSQL 15 (async SQLAlchemy 2 + asyncpg), migrations with Alembic
- **Cache / rate limiting:** Redis 7
- **ML:** OpenAI embeddings (`text-embedding-3-small`), Pinecone vector index
- **Tooling:** Poetry, pytest, Ruff, Docker Compose

## Project structure

```
app/
  api/v1/router.py      # v1 HTTP routes (thin handlers)
  core/                 # config, database, redis, security, exceptions
  models/               # SQLAlchemy models (Tenant, ApiKey)
  schemas/              # Pydantic request/response models
  services/             # business logic (TenantService)
  main.py               # app factory, lifespan, error handling, /health
alembic/                # database migrations
tests/                  # pytest suite (SQLite + fakeredis, no external services)
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

`OPENAI_API_KEY` and `PINECONE_API_KEY` are only required when `APP_ENV=production`. In production, any value containing `change-me` is rejected at startup.

### 2a. Run with Docker (recommended)

```bash
docker compose up --build
```

This starts PostgreSQL, Redis and the API, runs `alembic upgrade head`, and serves the app with auto-reload on http://localhost:8000.

### 2b. Run locally

Requires Python 3.11+, Poetry 2.x, and PostgreSQL + Redis running (for example `docker compose up postgres redis`).

```bash
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
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
```

Errors use one consistent shape:

```json
{ "error": { "code": "not_found", "message": "Tenant '...' not found" } }
```

## Security

- API keys look like `reco_<random>` and are shown **only once**, at creation.
- Only an HMAC-SHA256 hash of each key (keyed with `SECRET_KEY`) is stored, so a leaked database cannot be used to verify keys. Changing `SECRET_KEY` invalidates every existing key.
- Never commit `.env`. It is already in `.gitignore`.

## Testing and linting

```bash
poetry run pytest
poetry run ruff check .
```

Tests use SQLite and fakeredis, so no running Postgres or Redis is needed.

## Database migrations

```bash
poetry run alembic revision --autogenerate -m "describe change"
poetry run alembic upgrade head
```
