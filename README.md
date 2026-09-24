# RecoEngine

RecoEngine is a multi-tenant recommendation engine for any catalogue: jobs, dishes, products, courses or your own items.
You upload items, and they are embedded with OpenAI and indexed in Pinecone. You then get ranked recommendations by free text, by a similar item, or by a profile.
You get a REST API, an admin dashboard, SDKs for JavaScript and Python, and a production deployment for a single host.

**Live demo:** _coming soon_ — `https://app.recoengine.example.com` · **Docs:** _coming soon_ — `https://docs.recoengine.example.com`

## Architecture

```
                     ┌──────────────── nginx (TLS, rate limits, gzip, security headers) ───────────────┐
  browser ─────────► │  /            → dashboard  (Next.js 14, React Query, shadcn/ui)                 │
  your backend ────► │  /api/v1/*    → api        (FastAPI, async SQLAlchemy)                          │
  (SDKs, curl)       └──────────────────────────────────┬──────────────────────────────────────────────┘
                                                        │
             ┌──────────────────────┬───────────────────┼───────────────────┬─────────────────────┐
             ▼                      ▼                   ▼                   ▼                     ▼
      PostgreSQL 15            Redis 7            worker (polls        OpenAI embeddings     Pinecone
      tenants, API keys,       rate limits,       PENDING items,       text-embedding-3-     one serverless
      items, batches,          embedding cache,   embeds, upserts)     small (1536-d)        shared index,
      logs, feedback           result cache (5m)                                              namespace per tenant
                                                        │
                                   Prometheus ◄── /metrics (api, worker) ──► Grafana dashboard
```

Upload flow: `POST /items/upload` stores the items as `PENDING`. A background task or the worker builds text from the tenant's `searchable_fields`, embeds it, and upserts the vector with its `filter_fields` as metadata. The item is then `DONE`.
Query flow: filters are validated, then the result cache is checked. On a miss, the query is embedded (or the stored item vector is reused), Pinecone returns the top k, and the ranked results are logged for analytics.

## Quick start

Needs Docker, Node 20+, and OpenAI and Pinecone API keys.

```bash
git clone https://github.com/sachin-11/recommendation-engine-.git && cd recommendation-engine-
cp .env.example .env                       # set SECRET_KEY, OPENAI_API_KEY, PINECONE_API_KEY
docker compose up -d --build               # API :8000, Postgres, Redis, worker (runs migrations)
cd dashboard && cp .env.example .env.local && npm install && npm run dev   # dashboard :3000
./scripts/e2e_test.sh                      # from the repo root: register → upload → recommend → PASS/FAIL
```

Open http://localhost:3000 and register, or browse the API at http://localhost:8000/docs.

## SDK usage

**JavaScript / TypeScript** — `npm install @recoengine/sdk`

```ts
import { RecoEngineClient } from "@recoengine/sdk";

const client = new RecoEngineClient({ apiKey: process.env.RECO_API_KEY! });

const batch = await client.items.upload([
  { external_id: "job-1", title: "Python Dev", description: "Build FastAPI services", location: "Delhi" },
]);
if (batch.mode === "async") await client.items.waitForBatch(batch.batch_id);

const { results, query_id } = await client.recommend.byText("senior python developer", { topK: 10, filters: { location: "Delhi" } });
results.forEach((r) => console.log(r.rank, r.external_id, r.score_label));
await client.recommend.submitFeedback(query_id, results[0].external_id, "CLICK");
```

**Python** — `pip install recoengine`

```python
from recoengine import RecoEngineClient

client = RecoEngineClient(api_key="reco_xxxx")

batch = client.items.upload([
    {"external_id": "job_1", "title": "Python Dev", "description": "Build FastAPI services", "location": "Delhi"}
])
client.items.wait_for_batch(batch.batch_id)

results = client.recommend.by_text(query="senior python developer", top_k=10, filters={"location": "Delhi"})
for item in results.results:
    print(f"{item.rank}. {item.external_id} — {item.score_label}")
```

Both SDKs retry `429`/`503` with backoff and `Retry-After`, raise typed errors, and are fully typed. See [sdk/javascript](sdk/javascript/README.md) and [sdk/python](sdk/python/README.md).

## Documentation

| | |
|---|---|
| Guides: quickstart, authentication, HR / Food / E-commerce / custom domains | [`docs/`](docs/) (Docusaurus): `cd docs && npm install && npm start` |
| API reference | Generated from the FastAPI OpenAPI schema: `docs/static/openapi.json`, served at `/api-reference/` in the docs site and at `/docs` on a development API |
| Dashboard | [dashboard/README.md](dashboard/README.md) |
| Deployment guide | [docs/docs/deployment.mdx](docs/docs/deployment.mdx) |

Regenerate the API reference after changing endpoints: `python scripts/export_openapi.py`. CI fails if it is out of date.

## Repository layout

```
app/                FastAPI backend: api/, core/, middleware/, models/, schemas/, services/, workers/
alembic/            database migrations
tests/              backend tests (pytest; fake OpenAI/Pinecone, SQLite or Postgres)
dashboard/          Next.js admin dashboard and onboarding
sdk/javascript/     @recoengine/sdk (TypeScript, ESM + CJS)
sdk/python/         recoengine (sync + async, Pydantic v2)
docs/               Docusaurus documentation site
nginx/ monitoring/  reverse proxy, Prometheus and Grafana configuration
scripts/            export_openapi.py, aws-setup.sh, deploy.sh, e2e_test.sh
.github/workflows/  ci.yml (lint, types, tests, builds), deploy.yml (ECR → EC2)
```

## Deployment

Production runs on one EC2 host with `docker-compose.prod.yml`. The stack is the API, the worker, the dashboard, Postgres with nightly backups, Redis with AOF, nginx, Watchtower, Prometheus and Grafana. Images are built and pushed to ECR by `scripts/deploy.sh` or the GitHub **Deploy** workflow.

```bash
AWS_REGION=us-east-1 KEY_NAME=recoengine ./scripts/aws-setup.sh          # ECR, EC2, Elastic IP (+ --with-rds --with-redis)
DEPLOY_HOST=<ip> ECR_REGISTRY=<account>.dkr.ecr.us-east-1.amazonaws.com ./scripts/deploy.sh
```

Settings are listed in [`.env.production.example`](.env.production.example). The step-by-step guide is in [docs/docs/deployment.mdx](docs/docs/deployment.mdx).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and conventions. In short: branch from `main`, keep `ruff`, `mypy`, `pytest`, `eslint`, `tsc` and `jest` green, and open a pull request. CI runs all of them.

## License

[MIT](LICENSE)
