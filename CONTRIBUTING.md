# Contributing to RecoEngine

Thanks for helping! This repository holds the API, the dashboard, both SDKs and the docs. CI (`.github/workflows/ci.yml`) runs every check below on each pull request.

## Setup

```bash
cp .env.example .env            # OpenAI/Pinecone keys are only needed to embed for real
docker compose up -d --build    # API on :8000 with Postgres, Redis and the worker
```

The backend's tests need no API keys: OpenAI and Pinecone are replaced by in-memory fakes (`tests/fakes.py`).

## Checks per area

| Area | Commands |
|---|---|
| Backend (`app/`, `tests/`) | `docker compose exec app sh -c "ruff check . && ruff format --check . && mypy app && pytest -q"` |
| Backend on Postgres | `TEST_DATABASE_URL=postgresql+asyncpg://… pytest -q` |
| Dashboard (`dashboard/`) | `npm run lint && npm run typecheck && npm run build` |
| JS SDK (`sdk/javascript/`) | `npm run typecheck && npm test && npm run build` |
| Python SDK (`sdk/python/`) | `pip install -e ".[dev]" && ruff check . && mypy recoengine && pytest` (Python 3.9 and 3.12) |
| Docs (`docs/`) | `npm run typecheck && npm run build` |
| Against a running stack | `./scripts/e2e_test.sh` (uses real OpenAI and Pinecone; cleans up after itself) |

## Conventions

- **API changes:** update schemas and tests, add an operation entry (description and `operationId`) in `app/api/openapi.py`, then run `python scripts/export_openapi.py` and commit `docs/static/openapi.json`. Update both SDKs when a public endpoint changes.
- **Database changes:** add an Alembic migration (`alembic revision --autogenerate -m "…"`), review it, and check `alembic check` reports no drift.
- **Domain-specific behaviour** belongs in a tenant's domain config, not in engine code. The engine must stay domain-agnostic.
- Keep secrets out of commits: `.env`, `.env.production` and `.env.local` are ignored.
- Commit messages: imperative mood, e.g. "Add bulk delete endpoint".

## Pull requests

1. Branch from `main`.
2. Keep the change focused and include tests.
3. Describe what changed and how you verified it.
4. Wait for CI to pass before asking for review.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
