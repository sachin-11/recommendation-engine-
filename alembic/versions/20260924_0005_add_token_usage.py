"""add token_usage table and recommendation_logs.embedding_tokens

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USAGE_SOURCES = ("INGEST", "QUERY")


def upgrade() -> None:
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source", sa.Enum(*USAGE_SOURCES, name="usage_source"), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("api_calls", sa.Integer(), nullable=False),
        sa.Column("texts", sa.Integer(), nullable=False),
        sa.Column("cache_hits", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_token_usage_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_token_usage")),
        sa.UniqueConstraint(
            "tenant_id", "day", "source", "model", name="uq_token_usage_tenant_day_source_model"
        ),
    )
    op.add_column(
        "recommendation_logs",
        sa.Column("embedding_tokens", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("recommendation_logs", "embedding_tokens")
    op.drop_table("token_usage")
    sa.Enum(name="usage_source").drop(op.get_bind(), checkfirst=True)
