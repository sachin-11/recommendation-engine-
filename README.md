# RecoEngine

RecoEngine is a multi-tenant recommendation engine for any catalogue: jobs, dishes, products, courses or your own items.
You upload items, and they are embedded with OpenAI and indexed in Pinecone. You then get ranked recommendations by free text, by a similar item, by a profile, or by a plain-language question.

Ranking is a pipeline: hybrid vector and keyword search, then feedback re-ranking, personalization and an optional LLM re-ranker. A/B tests and an offline evaluation compare ranking variants.

You get a REST API, an admin dashboard, SDKs for JavaScript and Python, and Stripe billing. There are two deployments: a single EC2 host, or Kubernetes on EKS built with Terraform.

**Live demo:** https://reco.rasuonline.in (on EKS; up only while the cluster exists) · **Docs:** run locally, see [Documentation](#documentation)

## Features

| Module | What it adds |
|---|---|
| 1. Foundation | Multi-tenant FastAPI backend with async SQLAlchemy and Alembic; JWT sign-in; hashed API keys; per-tenant rate limits in Redis |
| 2. Embedding pipeline | Item upload (JSON and CSV) in batches; OpenAI `text-embedding-3-small`; one Pinecone namespace per tenant; background worker; embedding cache |
| 3. Query engine | `by-text`, `by-item`, `by-profile` and `batch` recommendations; validated metadata filters; 5-minute result cache; query logging |
| 4. Dashboard | Next.js 14 admin: onboarding, items, API keys, recommendation playground, analytics, settings |
| 5. SDKs, docs, production | `@recoengine/sdk` and `recoengine` (Python); Docusaurus docs with the API reference; EC2 deployment with nginx and TLS |
| 6. Account security | Email verification, password reset, sign-in lockout after repeated failures |
| 7. Teams | Members, roles (owner, admin, developer, viewer) and invitations; SMTP or SES email |
| 8. Observability | Prometheus metrics for HTTP, OpenAI, Pinecone and infrastructure; Grafana dashboard; alert rules |
| 9. Platform admin | Super-admin overview of all workspaces; workspace suspension; per-tenant limits |
| 10. Feedback ranking and A/B | Impressions and clicks per end user; time-decayed item stats; feedback re-ranking; personalization; A/B tests between ranking variants |
| 11. Hybrid search and evaluation | Postgres full-text search blended into vector search; golden query set; offline NDCG, recall and MRR per variant |
| 12. LLM re-ranking | OpenAI re-ranks the top candidates and gives a reason for each (structured output); fallback and caching; latency and cost per variant |
| 13. Ask | `POST /recommend/ask` turns a plain-language question into search text and checked filters; results and a written answer stream over Server-Sent Events |
| 14. Billing | Free and Pro plans with item, query and LLM limits; Stripe Checkout and Customer Portal; idempotent, order-safe webhooks |
| Deploy: EKS | Terraform for the VPC, EKS and ECR; Kubernetes manifests with Kustomize; ALB Ingress with HTTPS; API autoscaling; CI/CD with GitHub OIDC |

## Architecture

```
                     ┌──────── ingress: nginx (EC2) or AWS ALB (EKS), TLS ────────┐
  browser ─────────► │  /            → dashboard  (Next.js 14, React Query)       │
  your backend ────► │  /api/v1/*    → api        (FastAPI, async SQLAlchemy)     │
  (SDKs, curl)       └────────────────────────────┬───────────────────────────────┘
                                                  │
        ┌──────────────────┬──────────────────────┼──────────────────┬──────────────────┬──────────────┐
        ▼                  ▼                      ▼                  ▼                  ▼              ▼
  PostgreSQL 15        Redis 7               worker              OpenAI             Pinecone        Stripe
  tenants, keys,       rate limits,          embeds PENDING      embeddings,        one index,      Checkout,
  items, logs,         embedding and         items, rebuilds     LLM re-rank,       namespace       Portal,
  feedback, full-text  result caches         item stats          Ask, answers       per tenant      webhooks
                                                  │
                             Prometheus ◄── /metrics (api, worker) ──► Grafana
```

**Upload flow:**
1. `POST /items/upload` stores the items as `PENDING`.
2. The worker builds each item's text from the tenant's `searchable_fields` and embeds it.
3. The worker upserts the vector, with the item's `filter_fields` as metadata.
4. The item is marked `DONE`.

**Query flow:**
1. Validate the filters and check the result cache. The cache key includes the ranking settings.
2. On a miss, get candidates from vector search in Pinecone. With hybrid search on, keyword matches from Postgres are added: the score is the cosine similarity plus a weighted keyword score.
3. Re-rank by time-decayed feedback, then personalize toward items the end user liked, then (optionally) re-rank with the LLM.
4. Log the results and their impressions, tagged with the user's A/B variant, for analytics.

**Ask flow:**
1. An LLM turns the question into search text and filters.
2. The filters are checked against the tenant's fields. They are relaxed when nothing matches.
3. Results stream first, then the written answer.

## Quick start

Needs Docker, Node 20+, and OpenAI and Pinecone API keys.

```bash
git clone https://github.com/sachin-11/recommendation-engine-.git && cd recommendation-engine-
cp .env.example .env                       # set SECRET_KEY, OPENAI_API_KEY, PINECONE_API_KEY
docker compose up -d --build               # API :8000, Postgres, Redis, worker, Mailpit :8025
cd dashboard && cp .env.example .env.local && npm install && npm run dev   # dashboard :3000
./scripts/e2e_test.sh                      # from the repo root: register → upload → recommend → PASS/FAIL
```

Open http://localhost:3000 and register, or browse the API at http://localhost:8000/docs.
Mailpit catches account emails (verification, password reset, invitations): open http://localhost:8025.
Billing is off unless `BILLING_ENABLED=true`. To try Stripe in test mode, see [docs/docs/billing.mdx](docs/docs/billing.mdx).

## API at a glance

All endpoints are under `/api/v1`. Integrations call them with an API key (`X-API-Key`); the dashboard uses a JWT.

| Area | Endpoints |
|---|---|
| Auth and account | `/auth/*` (register, login, verify, reset, invitations), `/me`, `/me/members` |
| Items | `/items/upload`, `/items`, batches, `/index` |
| Recommend | `/recommend/by-text`, `/by-item`, `/by-profile`, `/batch`, `/feedback` |
| Ask | `/recommend/ask`, `/recommend/ask/stream` (SSE) |
| Analytics | `/analytics/overview`, `/feedback-summary`, `/usage`, `/tokens` |
| Evaluation | `/evaluation/queries`, `/evaluation/run` |
| Billing | `/billing`, `/billing/checkout`, `/billing/portal`, `/billing/sync`, `/billing/webhook` |
| Platform admin | `/admin/overview`, `/admin/workspaces` (suspend, activate, limits) |

The full reference is generated from the OpenAPI schema (see [Documentation](#documentation)).

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

Both SDKs:
- Retry `429` and `503` with backoff, honouring `Retry-After`.
- Raise typed errors.
- Are fully typed.

See [sdk/javascript](sdk/javascript/README.md) and [sdk/python](sdk/python/README.md).

## Documentation

| | |
|---|---|
| Guides: quickstart, authentication, teams, HR / Food / E-commerce / custom domains | [`docs/`](docs/) (Docusaurus): `cd docs && npm install && npm start` |
| Evaluation, billing, monitoring, platform admin | [evaluation](docs/docs/evaluation.mdx), [billing](docs/docs/billing.mdx), [monitoring](docs/docs/monitoring.mdx), [platform admin](docs/docs/platform-admin.mdx) |
| API reference | Generated from the FastAPI OpenAPI schema into `docs/static/openapi.json`. Served at `/api-reference/` in the docs site and at `/docs` on a development API |
| Dashboard | [dashboard/README.md](dashboard/README.md) |
| Deployment | EC2: [docs/docs/deployment.mdx](docs/docs/deployment.mdx). EKS: [infra/eks/README.md](infra/eks/README.md), with interview notes in [infra/eks/INTERVIEW.md](infra/eks/INTERVIEW.md) |

Regenerate the API reference after changing endpoints: `python scripts/export_openapi.py`. CI fails if it is out of date.

## Repository layout

```
app/                FastAPI backend: api/, core/, middleware/, models/, schemas/, services/, workers/
  services/recommendation/   query engine, hybrid and keyword search, re-rankers, personalization, A/B, Ask
  services/evaluation/       golden set and offline metrics
  services/billing/          plans, Stripe Checkout and Portal, webhooks
alembic/            database migrations
tests/              backend tests (pytest; fake OpenAI, Pinecone and Stripe; SQLite or Postgres)
dashboard/          Next.js admin dashboard and onboarding
sdk/javascript/     @recoengine/sdk (TypeScript, ESM + CJS)
sdk/python/         recoengine (sync + async, Pydantic v2)
docs/               Docusaurus documentation site
infra/eks/          Terraform (VPC, EKS, ECR, ACM, OIDC deploy role) and k8s/ manifests (Kustomize)
nginx/ monitoring/  reverse proxy, Prometheus and Grafana configuration
scripts/            export_openapi.py, aws-setup.sh, deploy.sh, e2e_test.sh
.github/workflows/  ci.yml (lint, types, tests, builds), deploy.yml (ECR → EC2), deploy-eks.yml (ECR → EKS)
```

## Deployment

### Single EC2 host

The host runs `docker-compose.prod.yml`:
- The API, worker and dashboard.
- Postgres with nightly backups, and Redis with AOF.
- nginx, Watchtower, Prometheus and Grafana.

Images are pushed to ECR by `scripts/deploy.sh` or the GitHub **Deploy** workflow.

```bash
AWS_REGION=us-east-1 KEY_NAME=recoengine ./scripts/aws-setup.sh          # ECR, EC2, Elastic IP (+ --with-rds --with-redis)
DEPLOY_HOST=<ip> ECR_REGISTRY=<account>.dkr.ecr.us-east-1.amazonaws.com ./scripts/deploy.sh
```

Settings are listed in [`.env.production.example`](.env.production.example). The step-by-step guide is [docs/docs/deployment.mdx](docs/docs/deployment.mdx).

### Kubernetes on EKS

Everything is in [infra/eks](infra/eks/README.md).

**Terraform** creates:
- A VPC with public and private subnets.
- EKS 1.35 with a managed node group.
- ECR repositories and a budget alert.
- An ACM certificate.
- IAM roles for the AWS Load Balancer Controller and for GitHub.

**Kustomize manifests** run:
- Postgres as a StatefulSet on gp3.
- Redis, Mailpit and a migration Job.
- The API (autoscaled from 2 to 5 pods by CPU), the worker and the dashboard.
- An ALB Ingress with HTTPS, redirecting HTTP.

**CI/CD:** when CI passes on `main`, the **Deploy to EKS** workflow:
1. Signs in to AWS with GitHub OIDC. No AWS keys are stored.
2. Builds the images, tags them with the commit, and pushes them to ECR.
3. Runs the migration and rolls out the new images.
4. Smoke-tests `/health`.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and conventions. In short: branch from `main`, keep `ruff`, `mypy`, `pytest`, `eslint`, `tsc` and `jest` green, and open a pull request. CI runs all of them.

## License

[MIT](LICENSE)
