"""complimentary Pro: a plan a platform admin gives without a subscription

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-02

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("comp_pro_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("comp_pro_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("comp_pro_reason", sa.String(500), nullable=True))


def downgrade() -> None:
    for column in ("comp_pro_reason", "comp_pro_until", "comp_pro_since"):
        op.drop_column("tenants", column)
