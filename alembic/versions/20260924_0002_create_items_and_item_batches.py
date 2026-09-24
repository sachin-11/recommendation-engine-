"""create items and item_batches tables; add api_keys.expires_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_STATUSES = ("PENDING", "PROCESSING", "DONE", "FAILED")
BATCH_STATUSES = ("PENDING", "PROCESSING", "DONE", "PARTIAL_FAIL")


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "item_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("processed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.Enum(*BATCH_STATUSES, name="batch_status"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_item_batches_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_batches")),
    )
    op.create_index(op.f("ix_item_batches_tenant_id"), "item_batches", ["tenant_id"], unique=False)

    op.create_table(
        "items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "embedding_status",
            sa.Enum(*EMBEDDING_STATUSES, name="embedding_status"),
            nullable=False,
        ),
        sa.Column("pinecone_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_items_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["item_batches.id"],
            name=op.f("fk_items_batch_id_item_batches"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_items_tenant_id_external_id"),
    )
    op.create_index(op.f("ix_items_batch_id"), "items", ["batch_id"], unique=False)
    op.create_index(
        "ix_items_tenant_id_embedding_status",
        "items",
        ["tenant_id", "embedding_status"],
        unique=False,
    )
    op.create_index(
        "ix_items_embedding_status_updated_at",
        "items",
        ["embedding_status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_items_embedding_status_updated_at", table_name="items")
    op.drop_index("ix_items_tenant_id_embedding_status", table_name="items")
    op.drop_index(op.f("ix_items_batch_id"), table_name="items")
    op.drop_table("items")
    op.drop_index(op.f("ix_item_batches_tenant_id"), table_name="item_batches")
    op.drop_table("item_batches")
    sa.Enum(name="embedding_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="batch_status").drop(op.get_bind(), checkfirst=True)
    op.drop_column("api_keys", "expires_at")
