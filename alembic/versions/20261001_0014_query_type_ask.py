"""query_type ASK, for questions in plain language

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-01

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE query_type ADD VALUE IF NOT EXISTS 'ASK'")


def downgrade() -> None:
    # Postgres cannot drop an enum value. Rows logged as ASK become TEXT, and the
    # unused value stays in the type, which is harmless.
    op.execute("UPDATE recommendation_logs SET query_type = 'TEXT' WHERE query_type = 'ASK'")
