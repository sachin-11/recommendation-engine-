"""Leans a query toward what one end user has liked before.

A user's taste is the mean vector of the items they recently clicked, liked, bought or
applied to (feedback carries the `user_id` of the query it was for). The query vector
becomes

    normalize((1 - w) * query + w * taste)

where w is `domain_config.ranking.personalization`. With no history the query is
unchanged, so an anonymous user and a new user get the same results.
"""

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.item import EmbeddingStatus, Item
from app.models.user_feedback import FeedbackType, UserFeedback

LIKED = (FeedbackType.CLICK, FeedbackType.THUMBS_UP, FeedbackType.PURCHASE, FeedbackType.APPLY)
# The newest liked items that make up a user's taste.
HISTORY_SIZE = 20


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else list(vector)


@dataclass(frozen=True)
class Taste:
    vector: list[float]  # unit length
    weight: float

    @classmethod
    def from_vectors(cls, vectors: Sequence[Sequence[float]], weight: float) -> "Taste | None":
        if not vectors:
            return None
        mean = [sum(column) / len(vectors) for column in zip(*vectors, strict=True)]
        return cls(normalize(mean), weight)

    def apply(self, query: Sequence[float]) -> list[float]:
        q = normalize(query)
        return normalize(
            [(1 - self.weight) * a + self.weight * b for a, b in zip(q, self.vector, strict=True)]
        )


async def liked_items(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: str, now: datetime | None = None
) -> list[str]:
    """Pinecone ids of the user's most recently liked items that are still embedded,
    newest first, from the last ITEM_STATS_WINDOW_DAYS."""
    since = (now or datetime.now(UTC)) - timedelta(days=settings.ITEM_STATS_WINDOW_DAYS)
    fb = UserFeedback
    last_liked = func.max(fb.created_at)
    rows = await session.execute(
        select(Item.pinecone_id)
        .join(
            fb,
            (fb.tenant_id == Item.tenant_id) & (fb.external_item_id == Item.external_id),
        )
        .where(
            fb.tenant_id == tenant_id,
            fb.end_user_id == user_id,
            fb.feedback_type.in_(LIKED),
            fb.created_at >= since,
            Item.embedding_status == EmbeddingStatus.DONE,
            Item.pinecone_id.is_not(None),
        )
        .group_by(Item.pinecone_id)
        .order_by(last_liked.desc())
        .limit(HISTORY_SIZE)
    )
    return [pinecone_id for pinecone_id in rows.scalars() if pinecone_id]
