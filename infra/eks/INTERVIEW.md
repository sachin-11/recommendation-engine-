# RecoEngine: interview notes

Everything I need to explain this project end to end:
- **Part 1** covers what the product is, what I built and why, and how each piece works.
- **Part 2** covers how I deployed it on Amazon EKS, step by step.
- The guide ends with production gaps and likely questions.

Live at the time: https://reco.rasuonline.in. It was a short-lived learning deployment,
destroyed afterwards with one command.

---

# Part 1 — The project

## The 30-second version

> "RecoEngine is a multi-tenant recommendation engine as a service. A company uploads its
> catalogue (jobs, products, dishes, courses) and gets ranked recommendations through a
> REST API, SDKs and an admin dashboard.
>
> Retrieval is semantic search with OpenAI embeddings in Pinecone, blended with
> Postgres keyword search. The results are then re-ranked: by what users clicked and bought,
> personalized per user, and optionally by an LLM that explains each choice. Users can also
> ask in plain language, and the answer streams back.
>
> I measure ranking quality offline with NDCG, recall and MRR, and online with A/B tests.
>
> It has teams and roles, Stripe billing, metrics and alerts, and I deployed it to Kubernetes
> on AWS with Terraform and a CI/CD pipeline."

## The problem it solves

Every product with a catalogue needs "show me relevant items": job portals, shops, food
apps, course sites. Building that well needs embeddings, a vector database, ranking,
feedback loops, evaluation and multi-tenancy. RecoEngine packages all of it behind one API.

**Domain-agnostic:**
- The engine never knows what a "job" or a "dish" is. Each workspace has a **domain
  config**:
  - Which fields to embed (`searchable_fields`).
  - Which fields can filter (`filter_fields`).
  - The ranking weights.
- The same code serves an HR site and a restaurant. The docs have HR, food and e-commerce
  guides.

**Who uses it:**
- **Developers** integrate the API or an SDK into their product.
- **The team** uses the dashboard: upload items, test queries, see analytics, run
  evaluations, manage members and billing.
- **Platform admins** (me, as the operator) see every workspace and can suspend one or set
  its limits.

## Tech stack

| Layer | Technology |
|---|---|
| API | Python 3.12, FastAPI (async), Pydantic v2, SQLAlchemy 2 async, Alembic |
| Data | PostgreSQL 15 (also full-text search), Redis 7 (cache, rate limits, locks) |
| AI | OpenAI `text-embedding-3-small` (1536-d), OpenAI chat model `gpt-5.4-mini` with structured JSON output, Pinecone serverless |
| Frontend | Next.js 14, React Query, TypeScript, zod |
| SDKs | TypeScript (ESM + CJS), Python (sync + async) |
| Payments | Stripe Checkout, Customer Portal, webhooks |
| Ops | Docker, Prometheus, Grafana, GitHub Actions |
| Cloud | AWS: EKS, ECR, ALB, ACM, IAM, VPC, managed with Terraform |
| Tests | pytest (fake OpenAI/Pinecone/Stripe, SQLite and real Postgres), jest |

## Architecture

```
  browser ──► dashboard (Next.js) ──┐
  customer backend (SDK / curl) ────┼──► API (FastAPI) ──► Postgres  (tenants, items, logs, feedback, full-text)
                                    │         │       ──► Redis     (caches, rate limits, locks)
                                    │         │       ──► OpenAI    (embeddings, LLM re-rank, Ask)
                                    │         │       ──► Pinecone  (vectors, one namespace per tenant)
                                    │         │       ──► Stripe    (billing)
                                    │      worker ──► embeds PENDING items, rebuilds item stats
                                    └── Prometheus scrapes /metrics ──► Grafana, alerts
```

## Two flows to explain on a whiteboard

**1. Upload (write path, asynchronous):**
1. `POST /items/upload` (JSON or CSV) validates the items and stores them as `PENDING` in
   Postgres, then returns a batch id at once.
2. A background task or the **worker** claims the items atomically
   (`PENDING → PROCESSING`), so many workers never embed the same item twice.
3. The text builder joins the item's `searchable_fields` into one text. OpenAI embeds it,
   with a Redis cache and retries with backoff.
4. The vector is upserted into Pinecone in the tenant's **namespace**, with the
   `filter_fields` as metadata. The item becomes `DONE`.
5. If OpenAI or Pinecone is down, the items go back to `PENDING` and are retried. Only an
   item-specific problem marks it `FAILED`.

**2. Recommend (read path, synchronous):**
1. Authenticate the API key, check the rate limit and the monthly quota.
2. Validate the filters against the tenant's `filter_fields`.
3. Check the **result cache** (Redis, 5 minutes). The cache key includes the ranking
   settings.
4. Build the query vector: embed the text, or reuse the item's stored vector
   (`by-item`). Lean the vector toward the user's taste (personalization).
5. **Retrieve:** Pinecone similarity search. If hybrid search is on, also run Postgres
   keyword search and blend the two.
6. **Re-rank:** add feedback signals. Optionally, the LLM re-reads the top candidates.
7. Format the results, log the query and its **impressions**, and return them with a
   `query_id`.
8. The client later sends **feedback** (click, like, purchase, …) with that `query_id`.
   That closes the loop.

## What I built, module by module

Each module was built in phases, tested and committed separately.

### Module 1 — Foundation and multi-tenancy
- Every table row belongs to a tenant. Every query is scoped by tenant id. In Pinecone,
  every tenant has its own namespace in one shared index.
  - **Why a namespace and not an index per tenant:** Pinecone limits indexes per project,
    while namespaces are unlimited.
- **API keys** look like `reco_<random>`. Only an **HMAC-SHA256** of the key is stored, so
  a leaked database cannot be used to verify or guess keys.
- **Rate limits** in Redis: requests per minute per key, plus items per day per tenant.
  - If Redis is down they **fail open**: availability over strictness.
- A **request id** on every request and in every log line (structlog), for tracing.

### Module 2 — Embedding pipeline
- Covered in the upload flow above. Key points:
  - Atomic claiming.
  - Retry vs fail.
  - Embedding cache (24 h).
  - Token truncation.
  - The Pinecone SDK is synchronous, so its calls run in a thread to keep the async event
    loop free.

### Module 3 — Query engine
- Three ways to query:
  - `by-text` (free text).
  - `by-item` ("more like this", reusing the stored vector, no OpenAI call).
  - `by-profile` (a user's attributes become text).
- Plus `batch` (many queries in one call).
- Filters are validated per tenant. OpenAI or Pinecone failures become a clean `503`, never
  partial results.

### Module 4 — Dashboard
- Next.js: onboarding wizard, items, API keys, a **playground** to try queries, analytics,
  settings.
- Forms and the domain config editor are validated with **zod**, using the same rules as
  the backend, so users see errors before a request is sent.

### Module 5 — SDKs, docs, production on EC2
- TypeScript and Python SDKs:
  - Typed.
  - Retry `429`/`503` with backoff and honour `Retry-After`.
  - `waitForBatch` for async uploads.
- Docusaurus docs. The API reference is generated from the OpenAPI schema, and **CI fails
  if it is stale**.
- A first production setup: Docker Compose on one EC2 host, with nginx (TLS, rate limits),
  nightly Postgres backups, Prometheus and Grafana.

### Module 6 — Account security
- **Email verification**: unverified accounts get a lower daily item limit.
- **Password reset.**
- **Sign-in lockout**: 5 failures lock the email for 15 minutes.
- Passwords are hashed with **scrypt**. The stored format includes its parameters, so they
  can be raised later.
- Email links use **single-use tokens**. Only their hash is stored, they expire, and a new
  link invalidates the old one.
- **Dashboard sessions** are API keys that expire after 7 days. The dashboard and
  integrations go through the same authentication.

### Module 7 — Teams and roles (RBAC)
- **Roles:**
  - `VIEWER`: read and run queries.
  - `DEVELOPER`: also manage items and API keys.
  - `ADMIN`: also settings and members.
  - `OWNER`: also transfer and delete; exactly one per workspace.
- **Invitations** by email. Email goes through SMTP or Amazon SES, with Mailpit in
  development.

### Module 8 — Observability
- **Prometheus metrics:**
  - HTTP requests by **route template** (not the raw path, so label cardinality stays
    bounded).
  - OpenAI and Pinecone latency and errors.
  - Items per status and the embedding backlog's age, computed at scrape time.
- **Grafana dashboard and alert rules**: API or worker down, a stuck embedding backlog,
  high error rate, slow recommendations, failing external services, token spikes.

### Module 9 — Platform admin
- A super-admin overview of all workspaces: usage, suspension, and per-workspace limits that
  override the plan.

### Module 10 — Feedback ranking and A/B tests (learning from users)
- **Impressions:** every result shown is logged with the end user's id. Feedback (click,
  like, dislike, purchase, apply, ignore) links to its query.
- **Item stats:**
  - The worker rebuilds them every 5 minutes, from **time-decayed** counts: a day's weight
    halves every 7 days, so recent behaviour counts more.
  - A Redis lock makes only one worker do it.
  - The table is swapped in one transaction, so readers never see half-built stats.
- **Re-ranking formula:**
  ```
  score = similarity
        + engagement × (item's click rate      − workspace average)
        + conversion × (item's conversion rate − workspace average)
        − negative   × (item's negative rate   − workspace average)
        + popularity × log-scaled impressions
  ```
  Rates are **smoothed toward the average**, so one lucky click on a rarely shown item does
  not jump it to the top, and a new item scores exactly its similarity (the cold start is
  handled).
- **Personalization:** a user's taste is the average vector of items they liked. The query
  vector becomes `normalize(0.8 × query + 0.2 × taste)`. With no history, the query is
  unchanged.
- **A/B test:**
  - A share of traffic gets CONTROL (similarity only), the rest RERANKED.
  - A user is assigned by **hashing workspace + user id**, so they always see the same
    variant.
  - The analytics show click and conversion rates with **95% confidence intervals** and a
    **two-proportion z-test p-value**.

### Module 11 — Hybrid search and offline evaluation
- **Why hybrid:**
  - Embeddings find meaning ("ML engineer" ≈ "machine learning developer").
  - They can miss exact terms: a product code, a library name, a person's name. Keyword
    search finds those.
- **Keyword search:** Postgres full-text search, using a GIN index and `ts_rank_cd`.
- **Blending:**
  ```
  score = cosine similarity + w × (keyword score / best keyword score)
  ```
  I did not use Reciprocal Rank Fusion. RRF replaces scores with small rank-based numbers,
  and the feedback re-ranker adds its adjustments on the similarity scale. Keeping the
  cosine as the base keeps every stage on one scale.
- **Offline evaluation:**
  - A **golden set** of queries, each with graded relevant items (1–3).
  - A run asks the engine for every query, once per **variant** (a set of ranking
    settings), and reports **NDCG@k, recall@k, MRR** and latency.
  - Runs bypass the result cache, personalization, A/B and quotas, so they measure only the
    ranking.
  - The dashboard compares the variants side by side.

### Module 12 — LLM re-ranking
- The last ranking stage:
  - The chat model reads the query and the **top 10** candidates (configurable 2–20).
  - It returns a **0–10 relevance score and a one-line reason** for each, in a strict **JSON
    schema**.
- **Guarded because it is expensive and slow:**
  - Only a few candidates.
  - A 4-second timeout.
  - A 1-hour cache.
  - On any failure the previous order is returned, so a request never fails because of the
    LLM.
- **Prompt injection:** item text is untrusted. The prompt marks it as data and the schema
  constrains the answer, so the worst a crafted item can do is move itself in the order.
- Evaluation reports **latency and cost per variant**, so I could show that the LLM improves
  NDCG and at what price.

### Module 13 — Ask (natural language + streaming)
- The user types, for example, *"remote python job, 3 to 5 years, Delhi or Pune is fine"*.
  The LLM turns that into:
  - search text `python developer`
  - filters `{location: [Remote, Delhi, Pune], experience_years: {gte: 3, lte: 5}}`
- **Validated, not trusted:**
  - The model sees only real filter fields and the values that exist.
  - Any field or value it invents is dropped and reported as `ignored`.
  - If the filters match nothing, they are **relaxed**.
  - If the LLM fails, the engine searches the question as written.
- **Server-Sent Events** (`/recommend/ask/stream`): events `interpretation` → `results` →
  `summary` (pieces of text) → `done`. The user sees results immediately while a 2–3
  sentence answer is still being written.
- **Why SSE and not WebSockets:** the stream is one-way (server to client), runs over plain
  HTTP and goes through proxies easily.

### Module 14 — Billing with Stripe (test mode)
- **Plans:**
  - **Free:** 1,000 items, 5,000 recommendations a month, no LLM features.
  - **Pro:** 50,000 items, 100,000 a month, LLM features.
- **Checkout and the Customer Portal are hosted by Stripe.** The app never sees a card.
- The plan changes **only from webhooks**, never from the browser redirect, because the user
  may close the tab before returning.
- **Webhook rules:**
  - **Signature** verified.
  - **Idempotent:** each event id is stored in the same transaction as its effect, so a
    duplicate delivery is skipped.
  - **Order-safe:** events can arrive out of order, so the handler **fetches the current
    subscription from Stripe** instead of trusting the event payload.
- `BILLING_ENABLED=false` (the default) means self-hosted: no limits, every feature on.

### Module 15 — Billing in the platform admin, and complimentary Pro
- **Two roles, two views:**
  - A workspace owner manages their own billing (self-serve, through Stripe).
  - A platform admin sees everyone's billing: the plan in force, the Stripe status and renewal,
    a link to the customer in Stripe, and counts of paid Pro, complimentary Pro and payments
    past due.
- **Complimentary Pro** is the exception to self-serve:
  - Pro without a subscription, for a demo, a partner or a support case.
  - For a number of days or until revoked, with an internal reason the workspace never sees.
  - It does not touch Stripe. When it ends, the workspace falls back to what it pays for, or
    Free.
- **One rule decides the plan** (): complimentary Pro first, then a paid plan
  while its subscription is in force, else Free. Limits, LLM features and the billing page all
  read that one function.
- **Security:** there is no API for a workspace to give itself Pro; only a platform admin
  session can, and integration API keys are refused on admin routes.

## Engineering practices worth mentioning

- **Tests:**
  - Fakes for OpenAI, Pinecone and Stripe, so tests are fast and free.
  - CI runs them on real Postgres too.
  - CI also checks that the migrations match the models (`alembic check`).
- **CI** runs ruff, mypy, pytest, eslint, tsc, jest, the SDK builds and the Docker builds.
- **Graceful degradation everywhere:**
  - Redis down → no cache and no rate limit, but the API works.
  - LLM down → the previous order.
  - Pinecone down → a clear 503.
- **Contract safety:** the OpenAPI docs are generated from the code, and CI fails if they
  are stale.
- **Small, reviewed steps:** every module was split into phases, each committed with a clear
  message.

---

# Part 2 — Deploying on Amazon EKS

How I took the app (FastAPI API, background worker, Next.js dashboard, Postgres, Redis) from
Docker Compose on my laptop to a Kubernetes cluster on AWS. The deployment uses Terraform,
HTTPS on my own domain, autoscaling and CI/CD. For each step: what I did, why, and how to
say it.

## The 30-second version

> "I wrote the infrastructure in Terraform: a VPC with public and private subnets, an EKS
> cluster with a managed node group, ECR for images, and IAM roles given to pods through EKS
> Pod Identity.
>
> The app runs as Kubernetes Deployments, with Postgres as a StatefulSet on an EBS volume.
> The AWS Load Balancer Controller creates an Application Load Balancer from an Ingress. It
> serves the app over HTTPS with an ACM certificate on my domain.
>
> The API autoscales with an HPA. A load test went from 2 to 5 pods with zero errors at
> about 340 requests a second.
>
> GitHub Actions deploys every commit that passes CI, in under 3 minutes. It uses OIDC, so
> no AWS keys are stored anywhere."

## Architecture

```
                      Internet
                         │  https://reco.rasuonline.in  (CNAME at GoDaddy → ALB)
                         ▼
        ┌──────────────────────────────────────────────┐
        │  Application Load Balancer  (public subnets) │   TLS: ACM certificate
        │  /api, /health → api      everything else →  │   HTTP → HTTPS redirect
        └───────────────┬───────────────────┬──────────┘
                        │ pod IPs           │
   ┌────────────────────┼───────────────────┼──────────────────────────┐
   │ EKS cluster        ▼                   ▼      private subnets     │
   │   namespace recoengine                                            │
   │   ┌──────────────┐  ┌─────────────┐  ┌──────────┐                 │
   │   │ api (2–5)    │  │ dashboard   │  │ worker   │  Deployments    │
   │   │ HPA on CPU   │  │ Next.js     │  │          │                 │
   │   └──────┬───────┘  └─────────────┘  └────┬─────┘                 │
   │          │                                │                       │
   │   ┌──────▼──────────┐  ┌─────────┐  ┌─────▼────┐                  │
   │   │ postgres-0      │  │ redis   │  │ mailpit  │                  │
   │   │ StatefulSet     │  └─────────┘  └──────────┘                  │
   │   │ + 5 GiB gp3 EBS │                                             │
   │   └─────────────────┘                                             │
   │   2 × t3.medium nodes (managed node group), 2 availability zones  │
   └───────────────────────────────┬───────────────────────────────────┘
                                   │ NAT gateway (outbound only)
                                   ▼
                      OpenAI, Pinecone, Stripe APIs
```

---

## Step 1 — Infrastructure as code (Terraform)

**What:** `vpc.tf`, `eks.tf`, `ecr.tf`, `budget.tf` (32 resources).

- **VPC** across 2 availability zones:
  - **Public subnets** hold the load balancer and the NAT gateway.
  - **Private subnets** hold the worker nodes. The nodes reach the internet through the NAT
    but cannot be reached from it.
- **EKS cluster** (Kubernetes 1.35) and a **managed node group** of two `t3.medium` nodes.
- **IAM roles**, one per job, each with only what it needs:
  - The cluster's role.
  - The nodes' role: join the cluster, pod networking, pull from ECR.
  - A role for the EBS driver.
- **Add-ons:**
  - Pod Identity agent.
  - EBS CSI driver (disks).
  - metrics-server (CPU numbers).
- **ECR** repositories, keeping the last 5 images.
- A **$20 budget alert** as a safety net.

**Why these choices:**
- Plain resources instead of community modules, so every piece is visible and explainable.
- **One NAT gateway** instead of one per zone: half the cost, fine for a demo.
- **EKS access entries** (the newer API) instead of the old `aws-auth` ConfigMap.
- **Pod Identity** instead of permissions on the whole node: each pod gets exactly one role.

**Say it:** *"Nodes are private; only the load balancer is public. Each component has its
own IAM role with least privilege, and pods get roles through Pod Identity, not node roles."*

---

## Step 2 — Apply and check the cluster

1. `terraform plan` first, then `terraform apply` of that saved plan. It takes about 15
   minutes; the control plane takes longest.
2. `aws eks update-kubeconfig`.
3. `kubectl get nodes`: two nodes `Ready`, in two zones. The system pods are running too:
   VPC CNI, CoreDNS, kube-proxy, the EBS driver and metrics-server.

**Say it:** *"I always apply a saved plan, so what runs is exactly what I reviewed."*

---

## Step 3 — Container images to ECR

- Built the API and dashboard images for `linux/amd64` (the node CPU) and pushed them to
  ECR.
- **Tag = git commit** (e.g. `e163940`), never `latest`:
  - You always know which code runs.
  - Rollback is redeploying an older tag.
- The API image also runs the worker: same code, different command.
- The dashboard calls the API on the **same origin** (`/api`), so no API URL is baked into
  the frontend at build time.

---

## Step 4 — Kubernetes manifests (Kustomize)

| Resource | Why |
|---|---|
| **Namespace** `recoengine` | Keeps everything together; deleting it removes the app |
| **StorageClass** `gp3` | EBS disks via the CSI driver; gp3 is cheaper and faster than gp2 |
| **Secret** | API keys and the DB password, created by a script from `.env`, never committed |
| **ConfigMap** | Non-secret settings |
| **Postgres StatefulSet** + volume claim | Stable name `postgres-0` and its own 5 GiB disk |
| **Redis, Mailpit** Deployments | Redis for cache and rate limits; Mailpit catches emails in the cluster |
| **Migration Job** | Runs `alembic upgrade head` once per deploy, not in every API replica |
| **API, worker, dashboard** Deployments + Services | The app |

**Key decisions (good interview questions):**

- **StatefulSet vs Deployment:**
  - The database needs a stable identity and its own disk that follows it.
  - The API, worker and dashboard are stateless.
- **Readiness vs liveness probe:**
  - Readiness = `/health`, which checks Postgres and Redis. A pod gets no traffic until they
    answer.
  - Liveness = only "is the port open". If liveness checked the database, a database outage
    would make Kubernetes restart every API pod. That fixes nothing and adds load.
- **Rolling update** with `maxUnavailable: 0`: a deploy never drops below the current
  number of ready pods.
- **`WaitForFirstConsumer`** on the StorageClass: the disk is created in the zone where the
  pod lands. An EBS volume can only attach to a node in its own zone.

**Something that went wrong:** the first API pod started before Postgres was ready.
1. It failed its startup.
2. Kubernetes restarted it, and then it worked: self-healing.

In production I would add an init container that waits for the database.

---

## Step 5 — Public URL and autoscaling

**Load balancer:**
- The plan was ingress-nginx, but the Kubernetes project **retired ingress-nginx in 2026**.
  I used the EKS-standard **AWS Load Balancer Controller** instead.
- The controller watches Ingress objects and creates an **Application Load Balancer**.
- **Path routing:** `/api` and `/health` go to the API, everything else to the dashboard.
- **`target-type: ip`:** the ALB sends traffic straight to pod IPs, skipping node ports.
  This works because the VPC CNI gives each pod a real VPC address.
- The controller gets its IAM role through **Pod Identity**: only its pods can create load
  balancers.

**Autoscaling (HPA):** API pods scale from 2 to 5 when average CPU passes 60% of the
requested 200m.

**Load test** (from a pod inside the cluster, 60 concurrent connections, 3 minutes):

| Result | |
|---|---|
| Requests | 61,496, **all 200** |
| Throughput | ~341 requests/second |
| Latency | average 176 ms, p99 0.8 s |
| Scaling | 2 → 5 pods within ~15 s; new pods ready in ~60 s; spread over both nodes |
| Scale down | back to 2 after the 5-minute stabilization window (deliberate, avoids flapping) |

**Something that went wrong:** the Deployment still set `replicas: 2`.
- Every deploy would then reset a scaled-up API to 2 and fight the HPA.
- When an HPA owns the count, the Deployment must not set `replicas`.
- Removing it from an already-applied manifest drops the Deployment to 1 for a moment. The
  HPA restored 2 within seconds.

---

## Step 6 — HTTPS on my own domain

- Terraform requested a free **ACM certificate** for `reco.rasuonline.in`, validated by
  **DNS**.
- The domain's DNS stays at GoDaddy, because the main site and email live there; I did not
  move it to Route 53. I added two CNAME records:
  1. ACM's validation record. ACM then issued the certificate.
  2. `reco` → the ALB's hostname.
- The Ingress got the certificate. The ALB listens on 443 and **redirects HTTP to HTTPS**.
- ACM renews the certificate by itself as long as the validation record stays.

**Something that went wrong:** the certificate stayed "pending" for a while.
1. Querying GoDaddy's own nameserver showed that the validation record had not been saved.
2. Once the record was added correctly, ACM issued the certificate within minutes.

---

## Step 7 — CI/CD with GitHub Actions

**Flow:** push to `main` → **CI** (lint, type checks, tests on SQLite and Postgres, builds).
If CI passes, a `check` job confirms that deploys are enabled, then **Deploy to EKS** runs:
1. Build both images and push them to ECR, tagged with the commit (with a layer cache in
   GitHub Actions).
2. Replace the migration Job and wait for it.
3. `kubectl apply -k` and wait for every rollout.
4. Smoke test `https://reco.rasuonline.in/health`.

The first successful run took **2 min 39 s** from start to a healthy new version. The pods
were replaced with no downtime.

**No stored AWS keys — OIDC:**
- GitHub gives each workflow run a signed, short-lived token. AWS trusts GitHub's issuer and
  lets **only this repository's `main` branch** assume a deploy role.
- The deploy role may push to the two ECR repositories and describe the cluster.
- Inside Kubernetes it has **edit rights in the `recoengine` namespace only**
  (`AmazonEKSEditPolicy` scoped to the namespace), not cluster admin.
- Cluster-wide pieces (namespace, StorageClass) moved to `k8s/cluster/`. An admin applies
  them once, so the pipeline never needs cluster-wide rights.
- The settings (role ARN, app URL, an on/off switch) are read from repository variables,
  with secrets as a fallback. None of them is a credential.

**Things that went wrong (good stories):**
1. **CI had been failing for weeks.**
   - Tests passed on my machine but failed in CI with `No module named 'app'`.
   - I ran tests as `python -m pytest`, which adds the current folder to the import path.
     CI ran plain `pytest`, which does not.
   - I reproduced it in a fresh `python:3.12` container with CI's exact command, then fixed
     it with `pythonpath = ["."]` in the pytest config.
   - Lesson: run CI's exact command locally, in a clean environment.
2. **Terraform ordering.**
   - The EKS access *policy association* failed with 404, because it was created in
     parallel with the *access entry* it attaches to.
   - Both referenced the IAM role directly, so Terraform saw no dependency between them.
   - Fixed by referencing the access entry's attribute, which makes Terraform create them
     in order.
3. **The deploy was skipped silently.**
   - The workflow read `vars.*`, but the settings had been saved as **secrets**, which
     `vars` never sees.
   - The job's `if:` was false, and GitHub showed no reason.
   - I added a `check` job that always runs and logs what it sees. It found the cause at
     once.
   - The workflow now accepts either a variable or a secret.
   - Lesson: make every skip explain itself.
4. **OIDC `AccessDenied`.**
   - AWS refused `AssumeRoleWithWebIdentity` even though the trust policy looked right.
   - **CloudTrail** showed the token's real subject:
     `repo:sachin-11@44609635/recommendation-engine-@1384500853:ref:refs/heads/main`.
   - GitHub now includes the owner's and repo's **immutable ids**.
   - I allowed that form in the trust policy. It is also safer: a deleted repo recreated
     under the same name cannot match it.
   - Lesson: debug IAM from CloudTrail, not by guessing.

---

## Cost and teardown

About **$6–8 a day**:
- EKS control plane ($0.10/hour).
- Two `t3.medium` nodes.
- NAT gateway.
- Load balancer.

Teardown, in this order:

```bash
# 1. stop CI/CD: set EKS_DEPLOY_ENABLED=false in GitHub
kubectl delete namespace recoengine                       # the controller deletes the ALB; the EBS disk goes too
helm uninstall aws-load-balancer-controller -n kube-system
terraform destroy
```

**Why the order matters:** Kubernetes creates some AWS resources itself: the ALB and the
EBS disk. Terraform does not know about them. Destroying the cluster first would leave them
running and billing, or block deleting the VPC.

---

# What I would add for real production

| Area | Now | Production |
|---|---|---|
| Database | Postgres pod, one disk, no backups on EKS | Amazon RDS with backups and Multi-AZ |
| Redis | One pod | ElastiCache with a replica |
| Email | Mailpit in the cluster | Amazon SES (already supported by the code) |
| Terraform state | Local file | S3 backend with locking |
| NAT | One gateway | One per availability zone |
| Secrets | Kubernetes Secret from `.env` | AWS Secrets Manager + External Secrets |
| Monitoring | Prometheus/Grafana exist for the EC2 setup; on EKS only `kubectl top` and logs | Prometheus + Grafana on the cluster, alerts, central logs (CloudWatch or Loki) |
| Environments | Straight to production | dev → staging → prod, with approval for prod |
| Nodes | Fixed node group | Karpenter or Cluster Autoscaler, Spot for workers |
| Startup order | Restart until Postgres is ready | Init container waiting for the database |
| Deploys | Rolling update | Canary or blue/green, automatic rollback on failed checks |
| Safety | — | PodDisruptionBudgets, NetworkPolicies, WAF on the ALB |
| Images | 1.4 GB API image | Slimmer image, vulnerability scanning in CI |
| Ranking | Feedback stats rebuilt every 5 minutes | Streaming updates, a learned ranking model trained on the logged feedback |

---

# Questions I should be ready for

## About the product and AI

**Why embeddings instead of keyword search alone?**
Embeddings match meaning: "ML engineer" finds "machine learning developer". Keyword search
alone misses that. Embeddings, in turn, can miss exact terms, which is why I built hybrid
search and measured the difference.

**How do you know the ranking is good?**
I measure it in two ways:
- **Offline:** a golden set of queries with graded relevance, scored with NDCG, recall and
  MRR for each variant before shipping.
- **Online:** A/B tests comparing click and conversion rates, with confidence intervals and
  a p-value, so a difference is not just noise.

**How do you handle cold start?**
- New items have no feedback, so they score exactly their similarity. The smoothing toward
  the workspace average keeps one lucky click from jumping an item to the top.
- New users have no taste vector, so their query is unchanged.

**Why not let the LLM rank everything?**
Cost and latency. Vector search narrows thousands of items to a handful in milliseconds.
The LLM reads only the top 10, with a timeout, a cache and a fallback. The evaluation shows
its quality gain against its added latency and cost.

**How do you stop the LLM from making things up?**
- Structured output with a JSON schema.
- Ask filters are checked against real fields and values; invented ones are dropped.
- The summary may only use the items it was given, each with its id.
- Item text is marked as data in the prompt (against prompt injection).

**How is tenant data isolated?**
- Every database query is scoped by tenant id.
- Each tenant has its own Pinecone namespace.
- API keys belong to one tenant.
- Cache keys include the tenant.
- Admin routes use a separate key.

**What happens when OpenAI or Redis is down?**
- Uploads stay `PENDING` and the worker retries them.
- Queries return a clean 503 when OpenAI or Pinecone is down.
- Without Redis, the cache and rate limits are skipped but the API keeps working.
- If the LLM fails, the previous order is returned.

**Why Stripe webhooks and not the redirect after payment?**
The redirect may never happen (the user closes the tab), and it can be faked. Webhooks are
signed. I made them idempotent and order-safe.

**How would you scale this to millions of items?**
- Pinecone serverless scales the vectors.
- Run more workers for ingestion: items are claimed atomically, so workers can be added
  freely.
- Use a read replica for analytics, and partition the log tables by month.
- Move the item-stats rebuild to incremental or streaming updates.

## About the deployment

**Why EKS and not just EC2 with Docker Compose?**
For a single small app, Compose on EC2 is cheaper and simpler, and that is the everyday
setup in this repo. I used EKS to learn and show orchestration: self-healing, rolling
updates, autoscaling, declarative config, and how Kubernetes integrates with AWS (IAM, load
balancers, disks).

**How does a request reach a pod?**
1. DNS: the `reco` CNAME points to the ALB.
2. The ALB ends TLS with the ACM certificate.
3. The target group sends the request straight to a pod IP (`target-type: ip`). This works
   because the VPC CNI gives each pod a VPC address.

**How do pods get AWS permissions?**
EKS Pod Identity. An association maps a namespace and service account to an IAM role, and
the Pod Identity agent gives that pod temporary credentials. No keys in the pod.

**What happens if a node dies?**
- The managed node group replaces the instance.
- Deployments reschedule their pods on the other node.
- The Postgres pod moves too, but its EBS disk can only attach in its own zone. That is one
  reason to use RDS in production.

**How do you roll back?**
Images are tagged with the commit, so there are two ways:
- Deploy the previous tag by re-running the pipeline on that commit.
- `kubectl rollout undo deployment/api` for an immediate rollback.

**How are migrations kept safe?**
A Job runs them once per deploy, before the new pods take traffic. During a rolling update,
the new code must work with both the old and the new schema (expand, then contract).

**Readiness vs liveness?**
See step 4: readiness gates traffic and may depend on the database. Liveness decides
restarts and must not.

**How did you keep costs under control?**
- Smallest viable nodes and one NAT gateway.
- A budget alert.
- A short lifetime.
- A teardown order that removes the resources Kubernetes created.
