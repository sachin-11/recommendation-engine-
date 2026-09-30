"""item_stats: time-decayed impressions and feedback per item

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_stats",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("external_item_id", sa.String(length=255), nullable=False),
        sa.Column("impressions", sa.Float(), nullable=False),
        sa.Column("clicks", sa.Float(), nullable=False),
        sa.Column("positives", sa.Float(), nullable=False),
        sa.Column("negatives", sa.Float(), nullable=False),
        sa.Column("conversions", sa.Float(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_item_stats_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "external_item_id", name=op.f("pk_item_stats")),
    )


def downgrade() -> None:
    op.drop_table("item_stats")
