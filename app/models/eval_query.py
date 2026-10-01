import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseEntity
from app.models.item import JSONType


class EvalQuery(BaseEntity):
    """One golden-set query: a text and the items a good answer contains, graded 1
    (relevant) to 3 (perfect). Offline evaluation measures rankings against these."""

    __tablename__ = "eval_queries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "query", name="uq_eval_queries_tenant_id_query"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    query: Mapped[str] = mapped_column(String(1000), nullable=False)
    # external_id -> grade
    relevant: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<EvalQuery {self.query[:40]!r} relevant={len(self.relevant)}>"
