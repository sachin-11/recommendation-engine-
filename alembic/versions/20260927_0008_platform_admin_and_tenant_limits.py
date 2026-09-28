"""platform admins, workspace suspension and per-workspace limits

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("tenants", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("suspended_reason", sa.String(length=500), nullable=True))
    op.add_column("tenants", sa.Column("max_items", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("monthly_query_limit", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("rate_limit_rpm", sa.Integer(), nullable=True))


def downgrade() -> None:
    for column in (
        "rate_limit_rpm",
        "monthly_query_limit",
        "max_items",
        "suspended_reason",
        "suspended_at",
    ):
        op.drop_column("tenants", column)
    op.drop_column("users", "is_platform_admin")
