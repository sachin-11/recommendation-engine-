"""Create or refresh the public demo workspace that POST /auth/demo signs visitors in to.

    python scripts/demo_workspace.py

Then set DEMO_ENABLED=true on the API. Safe to run again: it updates the workspace's
settings and items instead of creating a second one.

What it sets up:
- A workspace "RecoEngine Demo" (HR) with the jobs in scripts/demo_jobs.json. Items are
  stored as PENDING; the embedding worker embeds them within a minute or two.
- Its owner has no password, so nobody can sign in as the owner.
- A VIEWER user, DEMO_EMAIL, that visitors become: they can search, ask, give feedback and
  read analytics, but not change items, settings, keys, members or the golden set.
- Complimentary Pro with no end, so LLM re-ranking and Ask work.
- Limits that cap what anonymous visitors can cost: recommendations per month, requests per
  minute per session, and items. The API adds DEMO_QUERIES_PER_SESSION and
  DEMO_QUERIES_PER_DAY on top.
- A small golden set, so the Evaluation page has something to show.

In production (Railway):
    railway ssh --service api python scripts/demo_workspace.py
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models import Tenant, User  # noqa: E402
from app.models.base import utcnow  # noqa: E402
from app.models.tenant import DomainType  # noqa: E402
from app.models.user import Role  # noqa: E402
from app.schemas.evaluation import EvalQueryIn  # noqa: E402
from app.schemas.tenant import DomainConfig, RankingConfig, TenantCreate  # noqa: E402
from app.services.evaluation.service import EvaluationService  # noqa: E402
from app.services.item_service import ItemService  # noqa: E402
from app.services.tenant_service import TenantService  # noqa: E402

JOBS = Path(__file__).with_name("demo_jobs.json")

CONFIG = DomainConfig(
    primary_embedding_field="description",
    searchable_fields=["title", "description", "skills"],
    filter_fields=["location", "department", "employment_type", "experience_years"],
    item_label="job",
    # Hybrid search on, and the LLM re-ranker on, so a visitor sees every stage.
    ranking=RankingConfig(keyword=0.3, llm_rerank=True, llm_candidates=8),
)

# Golden queries: graded by which titles answer them (3 best, 1 acceptable).
GOLDEN: list[tuple[str, dict[str, int]]] = [
    ("python backend developer with fastapi", {"Backend Engineer (Python)": 3, "Full Stack": 1}),
    (
        "machine learning engineer to deploy models",
        {"Machine Learning Engineer": 3, "AI Engineer": 2, "Data Scientist": 1},
    ),
    (
        "build LLM apps with RAG and vector databases",
        {"AI Engineer": 3, "Machine Learning Engineer": 1},
    ),
    (
        "kubernetes and terraform cloud infrastructure",
        {"DevOps Engineer": 3, "Site Reliability Engineer": 2},
    ),
    ("react typescript frontend", {"Frontend Engineer (React)": 3, "Full Stack": 2}),
    ("hire engineers, technical recruiting", {"Technical Recruiter": 3, "HR Business Partner": 1}),
]


def golden_set(jobs: list[dict[str, Any]]) -> list[EvalQueryIn]:
    queries = []
    for query, grades in GOLDEN:
        relevant: dict[str, int] = {}
        for job in jobs:
            for fragment, grade in grades.items():
                if fragment in job["title"]:
                    relevant[job["external_id"]] = max(grade, relevant.get(job["external_id"], 0))
        queries.append(EvalQueryIn(query=query, relevant=relevant))
    return queries


def owner_email() -> str:
    local, _, domain = settings.DEMO_EMAIL.lower().partition("@")
    return f"{local}-owner@{domain}"


async def main(monthly_queries: int, rpm: int) -> int:
    jobs: list[dict[str, Any]] = json.loads(JOBS.read_text(encoding="utf-8"))
    async with AsyncSessionLocal() as session:
        viewer = await session.scalar(select(User).where(User.email == settings.DEMO_EMAIL.lower()))
        if viewer is None:
            tenant, _owner = await TenantService(session).create_tenant(
                TenantCreate(
                    name="RecoEngine Demo",
                    email=owner_email(),
                    domain_type=DomainType.HR,
                    domain_config=CONFIG,
                ),
                password_hash=None,  # nobody signs in as the owner
                verified=True,
                owner_name="RecoEngine",
            )
            viewer = User(
                tenant_id=tenant.id,
                email=settings.DEMO_EMAIL.lower(),
                name="Demo visitor",
                role=Role.VIEWER,
                password_hash=None,
                email_verified_at=utcnow(),
            )
            session.add(viewer)
            print(f"Created the demo workspace {tenant.id}")
        else:
            if viewer.role is not Role.VIEWER:
                print(f"{viewer.email} is {viewer.role}, not VIEWER; refusing.", file=sys.stderr)
                return 1
            found = await session.get(Tenant, viewer.tenant_id)
            assert found is not None
            tenant = found
            print(f"Refreshing the demo workspace {tenant.id}")

        tenant.domain_config = CONFIG.model_dump(mode="json")
        tenant.is_active = True
        tenant.comp_pro_since = tenant.comp_pro_since or utcnow()
        tenant.comp_pro_until = None
        tenant.comp_pro_reason = "Public demo"
        tenant.max_items = len(jobs) + 50
        tenant.monthly_query_limit = monthly_queries
        tenant.rate_limit_rpm = rpm
        await session.commit()

        batch = await ItemService(session, tenant).ingest_async(jobs)
        evaluation = EvaluationService(session, tenant)
        for query in golden_set(jobs):
            await evaluation.save_query(query)

    await engine.dispose()
    print(f"{len(jobs)} jobs queued for embedding (batch {batch.id}); the worker embeds them.")
    print(
        f"Limits: {monthly_queries} recommendations a month, {rpm} requests a minute per session."
    )
    print(f"Visitors sign in as {settings.DEMO_EMAIL} (VIEWER). Set DEMO_ENABLED=true on the API.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--monthly-queries", type=int, default=1000)
    parser.add_argument("--rpm", type=int, default=30, help="requests a minute per session key")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.monthly_queries, args.rpm)))
