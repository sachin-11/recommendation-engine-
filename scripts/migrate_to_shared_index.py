"""Move every tenant from its own Pinecone index (reco-<8 hex>) to a namespace in the shared
index (PINECONE_INDEX_NAME).

    python scripts/migrate_to_shared_index.py                    # plan only
    python scripts/migrate_to_shared_index.py --run              # re-embed into namespaces
    python scripts/migrate_to_shared_index.py --run --delete-legacy-indexes --yes

`--run` queues a rebuild for every tenant with items; the embedding worker (which must be
running, with the new code) re-embeds them into the tenant's namespace. Embeddings are
cached in Redis for 24 hours, so recently embedded items cost no OpenAI calls. The script
then waits until nothing is PENDING or PROCESSING and checks every tenant's namespace holds
as many vectors as it has DONE items.

`--delete-legacy-indexes` deletes the old per-tenant indexes, and only after that check
passes. Deleting an index cannot be undone.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models import EmbeddingStatus, Item, Tenant  # noqa: E402
from app.services.embedding.pinecone_service import get_pinecone_service  # noqa: E402
from app.services.item_service import ItemService  # noqa: E402

IN_FLIGHT = (EmbeddingStatus.PENDING, EmbeddingStatus.PROCESSING)


async def tenant_item_counts() -> list[tuple[Tenant, int, int]]:
    """(tenant, all items, DONE items) for tenants that have items."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(
                Tenant,
                func.count(Item.id),
                func.count(Item.id).filter(Item.embedding_status == EmbeddingStatus.DONE),
            )
            .join(Item, Item.tenant_id == Tenant.id)
            .group_by(Tenant.id)
            .order_by(Tenant.created_at)
        )
        return [(tenant, total, done) for tenant, total, done in rows.tuples()]


async def queue_rebuilds(tenants: list[tuple[Tenant, int, int]]) -> None:
    async with AsyncSessionLocal() as session:
        for tenant, total, _ in tenants:
            tenant = await session.merge(tenant)
            batch = await ItemService(session, tenant).rebuild()
            print(f"  queued {total:>6} items of {tenant.email} (batch {batch.id})")


async def wait_for_embedding(max_wait: float) -> bool:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as session:
            outstanding = await session.scalar(
                select(func.count()).select_from(Item).where(Item.embedding_status.in_(IN_FLIGHT))
            )
        if not outstanding:
            return True
        print(f"  {outstanding} items still embedding…")
        await asyncio.sleep(5)
    return False


async def verify_namespaces(tenants: list[tuple[Tenant, int, int]]) -> bool:
    store = get_pinecone_service()
    ok = True
    for tenant, _, _ in tenants:
        # Recount DONE after the rebuild; some items may legitimately have FAILED.
        async with AsyncSessionLocal() as session:
            done = await session.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.tenant_id == tenant.id, Item.embedding_status == EmbeddingStatus.DONE)
            )
        stats = await store.get_index_stats(tenant.id)
        vectors = stats["total_vector_count"]
        # Serverless stats are eventually consistent; give them a moment.
        for _ in range(12):
            if vectors >= (done or 0):
                break
            await asyncio.sleep(5)
            vectors = (await store.get_index_stats(tenant.id))["total_vector_count"]
        status = "ok" if vectors == done else "MISMATCH"
        ok = ok and vectors == done
        print(f"  {status:8} {tenant.email}: {done} DONE items, {vectors} vectors in namespace")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", action="store_true", help="queue re-embedding and wait")
    parser.add_argument("--delete-legacy-indexes", action="store_true")
    parser.add_argument("--yes", action="store_true", help="confirm deleting legacy indexes")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait")
    args = parser.parse_args()

    store = get_pinecone_service()
    tenants = await tenant_item_counts()
    legacy = await store.list_legacy_indexes()
    print(f"Shared index: {settings.PINECONE_INDEX_NAME}")
    print(f"Tenants with items: {len(tenants)}")
    for tenant, total, done in tenants:
        print(f"  {tenant.email}: {total} items ({done} DONE), namespace {tenant.id}")
    print(f"Legacy per-tenant indexes: {', '.join(legacy) or 'none'}")

    if not args.run:
        print("\nPlan only. Re-run with --run to migrate.")
        return 0

    try:
        print("\nQueueing rebuilds:")
        await store.ensure_index_exists()
        await queue_rebuilds(tenants)
        print("\nWaiting for the worker:")
        if not await wait_for_embedding(args.timeout):
            print("Timed out: is the embedding worker running with the new code?")
            return 1
        print("\nVerifying namespaces:")
        if not await verify_namespaces(tenants):
            print("\nNamespaces do not match the database; legacy indexes were kept.")
            return 1

        if args.delete_legacy_indexes and legacy:
            if not args.yes:
                print("\nAdd --yes to delete: " + ", ".join(legacy))
                return 1
            print("\nDeleting legacy indexes:")
            for name in legacy:
                await store.delete_legacy_index(name)
                print(f"  deleted {name}")
        print("\nMigration complete.")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
