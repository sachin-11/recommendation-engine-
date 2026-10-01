"""items.search_text and its full-text GIN index, for keyword search

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-01

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.embedding.text_builder import TextBuilder

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_CHUNK = 1000


def upgrade() -> None:
    op.add_column("items", sa.Column("search_text", sa.Text(), nullable=True))
    op.execute(
        "CREATE INDEX ix_items_search_vector ON items "
        "USING gin (to_tsvector('simple', coalesce(search_text, '')))"
    )

    # Items embedded before this migration: same text the pipeline now stores.
    bind = op.get_bind()
    builder = TextBuilder()
    configs = dict(bind.execute(sa.text("SELECT id, domain_config FROM tenants")).all())
    last_id = uuid.UUID(int=0)
    while True:
        rows = bind.execute(
            sa.text(
                "SELECT id, tenant_id, raw_data FROM items "
                "WHERE embedding_status = 'DONE' AND id > :last "
                "ORDER BY id LIMIT :limit"
            ),
            {"last": last_id, "limit": BACKFILL_CHUNK},
        ).all()
        if not rows:
            break
        bind.execute(
            sa.text("UPDATE items SET search_text = :text WHERE id = :id"),
            [
                {"id": r.id, "text": builder.build_keyword_text(r.raw_data, configs[r.tenant_id])}
                for r in rows
            ],
        )
        last_id = rows[-1].id


def downgrade() -> None:
    op.drop_index("ix_items_search_vector", table_name="items")
    op.drop_column("items", "search_text")
