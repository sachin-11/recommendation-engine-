# Backend on Railway

The API, the worker, Postgres and Redis run on [Railway](https://railway.com). The
dashboard runs on Vercel. This is the everyday hosting; [infra/eks](../eks/README.md) was a
short-lived Kubernetes deployment for learning.

```
  dashboard (Vercel) ──HTTPS──► api  (Dockerfile, $PORT, /health)   ┐
                                worker (same image, embedding worker) ├─ private network
                                Postgres 18 (volume)                  │  *.railway.internal
                                Redis 8 (volume)                      ┘
  api, worker ──HTTPS──► OpenAI, Pinecone, Resend (email)
```

| Service | Source | Notes |
|---|---|---|
| `api` | GitHub `main`, `Dockerfile` | `alembic upgrade head` runs as the pre-deploy command, once per deploy; healthcheck `/health` |
| `worker` | GitHub `main`, `Dockerfile` | Start command `python -m app.workers.embedding_worker`; restarts always |
| `Postgres` | Railway template | Private only; the app reaches it at `postgres.railway.internal` |
| `Redis` | Railway template | Private only |

## Deploys

- A push to `main` triggers a deploy of both services.
- `checkSuites` makes Railway wait for the commit's GitHub checks (the CI workflow), so a
  commit that fails CI is never deployed.
- Railway switches traffic to the new `api` deployment only after `/health` answers 200,
  so a broken build does not replace a working one.

## Settings

All settings live on the `api` service. The `worker` refers to them as `${{api.NAME}}`,
so each secret is stored in one place.

| Variable | Value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}` (the app needs the asyncpg scheme, so it is composed from Railway's parts) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `APP_ENV` | `production` (API docs off; startup refuses unsafe settings) |
| `WEB_CONCURRENCY` | `1` (one Uvicorn process: enough for a demo, half the memory) |
| `SECRET_KEY` | random, generated once |
| `OPENAI_API_KEY`, `PINECONE_*`, `EMBEDDING_*` | from `.env` |
| `EMAIL_BACKEND`, `EMAIL_FROM`, `RESEND_API_KEY` | `resend`, `RecoEngine <no-reply@rasuonline.in>`, a Resend key with sending access to that domain |
| `DASHBOARD_URL`, `ALLOWED_ORIGINS` | the dashboard's public URL |
| `BILLING_ENABLED` | `true`: Free and Pro plans, Stripe in **test mode** (card `4242 4242 4242 4242`) |
| `STRIPE_SECRET_KEY`, `RECO_STRIPE_PRO_*_PRICE_ID` | the test-mode key and the Pro prices |
| `RECO_STRIPE_WEBHOOK_SECRET` | signing secret of the Stripe webhook endpoint `https://reco.rasuonline.in/api/v1/billing/webhook` (8 events; see [billing docs](../../docs/docs/billing.mdx)) |

**Why Resend and not SMTP:** Railway blocks outbound SMTP (ports 25, 465 and 587) below its
Pro plan. `EMAIL_BACKEND=resend` sends through Resend's HTTPS API instead. The domain
`rasuonline.in` is verified in Resend with DKIM, SPF (on `send.`) and the existing DMARC
record, all at GoDaddy.

## Public demo

The sign-in page offers **Explore the live demo**: a 2-hour, read-only session in a demo
workspace, without sign-up. To set it up (once, and again to refresh it):

```bash
railway ssh --service api python scripts/demo_workspace.py
railway variables --service api --set DEMO_ENABLED=true
```

The script creates the workspace "RecoEngine Demo": 125 jobs, hybrid search and LLM
re-ranking on, complimentary Pro, and limits that cap what anonymous visitors can cost (3,000
recommendations a month, 30 requests a minute per session). Visitors sign in as its VIEWER,
so they can search, ask and read analytics, but cannot change anything.

## Setting it up again

```bash
railway init --name recoengine                # project, linked to this folder
railway add --database postgres
railway add --database redis
railway add --service api
railway add --service worker
# variables: see the table above (railway variables --service api --skip-deploys --set K=V)
# services.json has this project's service ids; put the new ids in, then:
railway environment edit -m "api and worker from GitHub" < infra/railway/services.json
railway domain --service api                  # public https://…up.railway.app URL
```

[services.json](services.json) is the configuration that was applied: the repository and
branch, the Dockerfile builder, the pre-deploy migration, the healthcheck and the worker's
start command. Run from a script, `railway environment edit` reads such a JSON patch on
stdin.

## Cost

Railway's Hobby plan is $5 a month, including $5 of usage. Four small services cost about
$5–10 a month. Each service can be removed in the dashboard, and `railway delete` removes
the whole project.
