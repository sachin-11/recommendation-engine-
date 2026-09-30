"""recommendation impressions, end user ids and ranking variants

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recommendation_logs", sa.Column("end_user_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "recommendation_logs",
        sa.Column(
            "ranking_variant", sa.String(length=32), server_default="control", nullable=False
        ),
    )
    op.create_index(
        "ix_recommendation_logs_tenant_id_end_user_id",
        "recommendation_logs",
        ["tenant_id", "end_user_id"],
        unique=False,
    )
    op.add_column("user_feedback", sa.Column("end_user_id", sa.String(length=255), nullable=True))

    op.create_table(
        "recommendation_impressions",
        sa.Column("recommendation_log_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("external_item_id", sa.String(length=255), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_log_id"],
            ["recommendation_logs.id"],
            name=op.f("fk_recommendation_impressions_recommendation_log_id_recommendation_logs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_recommendation_impressions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "recommendation_log_id", "rank", name=op.f("pk_recommendation_impressions")
        ),
    )
    op.create_index(
        "ix_recommendation_impressions_tenant_id_created_at",
        "recommendation_impressions",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_recommendation_impressions_tenant_id_external_item_id",
        "recommendation_impressions",
        ["tenant_id", "external_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("recommendation_impressions")
    op.drop_column("user_feedback", "end_user_id")
    op.drop_index("ix_recommendation_logs_tenant_id_end_user_id", table_name="recommendation_logs")
    op.drop_column("recommendation_logs", "ranking_variant")
    op.drop_column("recommendation_logs", "end_user_id")
