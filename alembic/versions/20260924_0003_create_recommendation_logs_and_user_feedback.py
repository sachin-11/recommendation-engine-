"""create recommendation_logs and user_feedback tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUERY_TYPES = ("TEXT", "ITEM_ID", "PROFILE")
FEEDBACK_TYPES = ("CLICK", "THUMBS_UP", "THUMBS_DOWN", "PURCHASE", "APPLY", "IGNORE")


def upgrade() -> None:
    op.create_table(
        "recommendation_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("query_type", sa.Enum(*QUERY_TYPES, name="query_type"), nullable=False),
        sa.Column("query_input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("results_count", sa.Integer(), nullable=False),
        sa.Column("top_result_external_id", sa.String(length=255), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("filters_applied", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_recommendation_logs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_logs")),
    )
    op.create_index(
        "ix_recommendation_logs_tenant_id_created_at",
        "recommendation_logs",
        ["tenant_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_log_id", sa.Uuid(), nullable=False),
        sa.Column("external_item_id", sa.String(length=255), nullable=False),
        sa.Column("feedback_type", sa.Enum(*FEEDBACK_TYPES, name="feedback_type"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_user_feedback_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_log_id"],
            ["recommendation_logs.id"],
            name=op.f("fk_user_feedback_recommendation_log_id_recommendation_logs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_feedback")),
    )
    op.create_index(
        op.f("ix_user_feedback_recommendation_log_id"),
        "user_feedback",
        ["recommendation_log_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_feedback_tenant_id_created_at",
        "user_feedback",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_feedback_tenant_id_created_at", table_name="user_feedback")
    op.drop_index(op.f("ix_user_feedback_recommendation_log_id"), table_name="user_feedback")
    op.drop_table("user_feedback")
    op.drop_index("ix_recommendation_logs_tenant_id_created_at", table_name="recommendation_logs")
    op.drop_table("recommendation_logs")
    sa.Enum(name="feedback_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="query_type").drop(op.get_bind(), checkfirst=True)
