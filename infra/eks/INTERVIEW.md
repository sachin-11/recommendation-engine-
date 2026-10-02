# Deploying RecoEngine on Amazon EKS: interview notes

How I took the app (FastAPI API, background worker, Next.js dashboard, Postgres, Redis) from
Docker Compose on my laptop to a Kubernetes cluster on AWS, with Terraform, HTTPS on my own
domain, autoscaling and CI/CD. Each step: what I did, why, and how to say it.

Live at the time: https://reco.rasuonline.in. It was a short-lived learning deployment,
destroyed afterwards with one command.

---

## The 30-second version

> "I wrote the infrastructure in Terraform: a VPC with public and private subnets, an EKS
> cluster with a managed node group, ECR for images, and IAM roles given to pods through EKS
> Pod Identity. The app runs as Kubernetes Deployments, with Postgres as a StatefulSet on an
> EBS volume. An Application Load Balancer, created by the AWS Load Balancer Controller from
> an Ingress, serves it over HTTPS with an ACM certificate on my domain. The API autoscales
> with an HPA; a load test went from 2 to 5 pods with zero errors at about 340 requests a
> second. GitHub Actions deploys every commit that passes CI, using OIDC, so no AWS keys
> are stored anywhere."

---

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

- **VPC** across 2 availability zones. **Public subnets** hold the load balancer and the NAT
  gateway; **private subnets** hold the worker nodes, which can reach the internet through
  the NAT but cannot be reached from it.
- **EKS cluster** (Kubernetes 1.35) and a **managed node group** of two `t3.medium` nodes.
- **IAM roles**, one per job, each with only what it needs: the cluster's role, the nodes'
  role (join the cluster, pod networking, pull from ECR), and a role for the EBS driver.
- **Add-ons:** Pod Identity agent, EBS CSI driver (disks), metrics-server (CPU numbers).
- **ECR** repositories, keeping the last 5 images. A **$20 budget alert** as a safety net.

**Why these choices:**
- Plain resources instead of community modules, so every piece is visible and explainable.
- **One NAT gateway** instead of one per zone: half the cost; fine for a demo.
- **EKS access entries** (the newer API) instead of the old `aws-auth` ConfigMap.
- **Pod Identity** instead of giving permissions to the whole node: a pod gets exactly one
  role.

**Say it:** *"Nodes are private; only the load balancer is public. Each component has its
own IAM role with least privilege, and pods get roles through Pod Identity, not node roles."*

---

## Step 2 — Apply and check the cluster

`terraform plan` first, then `terraform apply` of that saved plan (~15 minutes; the control
plane takes longest). Then `aws eks update-kubeconfig` and `kubectl get nodes`: two nodes
`Ready`, in two zones, plus the system pods (VPC CNI, CoreDNS, kube-proxy, EBS driver,
metrics-server).

**Say it:** *"I always apply a saved plan, so what runs is exactly what I reviewed."*

---

## Step 3 — Container images to ECR

- Built the API and dashboard images for `linux/amd64` (the node CPU) and pushed to ECR.
- **Tag = git commit** (e.g. `e163940`), never `latest`: you always know which code runs,
  and rollback is redeploying an older tag.
- The API image also runs the worker (same code, different command).
- The dashboard calls the API on the **same origin** (`/api`), so no API URL is baked into
  the frontend at build time.

---

## Step 4 — Kubernetes manifests (Kustomize)

| Resource | Why |
|---|---|
| **Namespace** `recoengine` | Everything together; deleting it removes the app |
| **StorageClass** `gp3` | EBS disks via the CSI driver; gp3 is cheaper and faster than gp2 |
| **Secret** | API keys and the DB password, created by a script from `.env`, never committed |
| **ConfigMap** | Non-secret settings |
| **Postgres StatefulSet** + volume claim | Stable name `postgres-0` and its own 5 GiB disk |
| **Redis, Mailpit** Deployments | Cache/rate limits; catches emails in the cluster |
| **Migration Job** | `alembic upgrade head` once per deploy, not by every API replica |
| **API, worker, dashboard** Deployments + Services | The app |

**Key decisions (good interview questions):**

- **StatefulSet vs Deployment:** the database needs a stable identity and its own disk that
  follows it; the API, worker and dashboard are stateless.
- **Readiness vs liveness probe:**
  - Readiness = `/health`, which checks Postgres and Redis: a pod gets no traffic until
    they answer.
  - Liveness = only "is the port open". If liveness checked the database, a database
    outage would make Kubernetes restart every API pod, which fixes nothing and adds load.
- **Rolling update** with `maxUnavailable: 0`: a deploy never drops below the current
  number of ready pods.
- **`WaitForFirstConsumer`** on the StorageClass: the disk is created in the zone where the
  pod lands (an EBS volume can only attach to a node in its own zone).

**Something that went wrong:** the first API pod started before Postgres was ready, failed
its startup, and Kubernetes restarted it, after which it worked: self-healing. In
production I would add an init container that waits for the database.

---

## Step 5 — Public URL and autoscaling

**Load balancer:** the plan was ingress-nginx, but the Kubernetes project **retired
ingress-nginx in 2026**, so I used the EKS-standard **AWS Load Balancer Controller**
instead. It watches Ingress objects and creates an **Application Load Balancer**:
- Path routing: `/api` and `/health` to the API, everything else to the dashboard.
- **`target-type: ip`:** the ALB sends traffic straight to pod IPs (each pod has a real VPC
  address through the VPC CNI), skipping node ports.
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

**Something that went wrong:** the Deployment still said `replicas: 2`. Then every deploy
would reset a scaled-up API to 2 and fight the HPA. When an HPA owns the count, the
Deployment must not set `replicas`. (Removing it from an already-applied manifest drops
the Deployment to 1 for a moment; the HPA restored 2 within seconds.)

---

## Step 6 — HTTPS on my own domain

- Terraform requested a free **ACM certificate** for `reco.rasuonline.in`, validated by
  **DNS**.
- The domain's DNS stays at GoDaddy (the main site and email live there; I did not move it
  to Route 53). I added two CNAME records:
  1. ACM's validation record → certificate issued.
  2. `reco` → the ALB's hostname.
- The Ingress got the certificate: ALB listens on 443, **redirects HTTP to HTTPS**.
- ACM renews the certificate by itself while the validation record stays.

**Something that went wrong:** the certificate stayed "pending" for a while. Querying
GoDaddy's own nameserver showed the validation record had not been saved; after it was
added correctly, ACM issued the certificate within minutes.

---

## Step 7 — CI/CD with GitHub Actions

**Flow:** push to `main` → **CI** (lint, type checks, tests on SQLite and Postgres, builds)
→ if it passes, **Deploy to EKS**:
1. Build both images, push to ECR tagged with the commit (layer cache in GitHub Actions).
2. Replace the migration Job and wait for it.
3. `kubectl apply -k`, wait for every rollout.
4. Smoke test `https://reco.rasuonline.in/health`.

**No stored AWS keys — OIDC:**
- GitHub gives each workflow run a signed, short-lived token. AWS trusts GitHub's issuer
  and lets **only this repository's `main` branch** assume a deploy role.
- The deploy role may push to the two ECR repositories and describe the cluster. Inside
  Kubernetes it has **edit rights in the `recoengine` namespace only**
  (`AmazonEKSEditPolicy` scoped to the namespace), not cluster admin.
- Cluster-wide pieces (namespace, StorageClass) moved to `k8s/cluster/`, applied once by an
  admin, so the pipeline never needs cluster-wide rights.

**Something that went wrong — twice:**
1. **CI had been failing for weeks.** Tests passed on my machine but failed in CI with
   `No module named 'app'`. I ran tests as `python -m pytest` (which adds the current folder
   to the import path); CI ran plain `pytest` (which does not). I reproduced it in a fresh
   `python:3.12` container with CI's exact command, then fixed it with `pythonpath = ["."]`
   in the pytest config. Lesson: run CI's exact command locally, in a clean environment.
2. **Terraform ordering:** the EKS access *policy association* failed with 404 because it
   was created in parallel with the *access entry* it attaches to. Both referenced the IAM
   role directly, so Terraform saw no dependency. Fixed by referencing the access entry's
   attribute, which makes Terraform create them in order.

---

## Cost and teardown

About **$6–8 a day**: EKS control plane ($0.10/hour), two `t3.medium` nodes, NAT gateway,
load balancer. Teardown, in this order:

```bash
# 1. stop CI/CD: set EKS_DEPLOY_ENABLED=false in GitHub
kubectl delete namespace recoengine                       # the controller deletes the ALB; the EBS disk goes too
helm uninstall aws-load-balancer-controller -n kube-system
terraform destroy
```

**Why the order matters:** Kubernetes creates some AWS resources itself (the ALB, the EBS
disk). Terraform does not know about them. Destroying the cluster first would leave them
running and billing, or block deleting the VPC.

---

## What I would add for real production

| Area | Now | Production |
|---|---|---|
| Database | Postgres pod, one disk, no backups | Amazon RDS with backups and Multi-AZ |
| Email | Mailpit in the cluster | Amazon SES |
| Terraform state | Local file | S3 backend with locking |
| NAT | One gateway | One per availability zone |
| Secrets | Kubernetes Secret from `.env` | AWS Secrets Manager + External Secrets |
| Monitoring | `kubectl top`, logs | Prometheus + Grafana, alerts, CloudWatch Container Insights |
| Nodes | Fixed node group | Karpenter or Cluster Autoscaler, Spot for workers |
| Startup order | Restart until Postgres is ready | Init container waiting for the database |
| Deploys | Rolling update | Canary or blue/green, automatic rollback on failed checks |
| Images | 1.4 GB API image | Slimmer image, vulnerability scanning in CI |

---

## Questions I should be ready for

**Why EKS and not just EC2 with Docker Compose?**
For a single small app, Compose on EC2 is cheaper and simpler, and that is the everyday
setup in this repo. I used EKS to learn and show orchestration: self-healing, rolling
updates, autoscaling, declarative config, and how it integrates with AWS (IAM, load
balancers, disks).

**How does a request reach a pod?**
DNS (`reco` CNAME) → ALB → (TLS ends at the ALB with the ACM certificate) → target group →
pod IP directly (`target-type: ip`, possible because the VPC CNI gives pods VPC addresses).

**How do pods get AWS permissions?**
EKS Pod Identity: an association maps a namespace + service account to an IAM role; the
Pod Identity agent hands that pod temporary credentials. No keys in the pod.

**What happens if a node dies?**
The managed node group replaces the instance. Deployments reschedule their pods on the
other node. The Postgres pod moves too, but its EBS disk can only attach in its own zone,
which is one reason to use RDS in production.

**How do you roll back?**
Images are tagged with the commit, so deploy the previous tag (re-run the pipeline on that
commit), or `kubectl rollout undo deployment/api` for an immediate rollback.

**How are migrations kept safe?**
A Job runs them once per deploy before the new pods take traffic. New code must work with
both the old and new schema during a rolling update (expand, then contract).

**Readiness vs liveness?**
See step 4: readiness gates traffic and may depend on the database; liveness decides
restarts and must not.

**How did you keep costs under control?**
Smallest viable nodes, one NAT gateway, a budget alert, a short lifetime, and a teardown
order that removes resources Kubernetes created.
