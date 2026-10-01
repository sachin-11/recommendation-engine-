"""Keyword (full-text) search over a workspace's embedded items.

Vector search finds items that mean the same thing; it can miss an exact term such as a
product code, a library name or a person's name. Keyword search finds those. Phase 2
fuses both result lists.

On Postgres it uses the GIN index on items.search_text: any query word may match (OR),
and ts_rank_cd ranks items with more, closer and repeated matches higher. That is a
lexical ranking in the BM25 family, not BM25 itself. SQLite (tests and local runs
without Postgres) scores in Python with the same idea.

Filters are the vector search's, applied to the same item metadata with Pinecone's
semantics. They are applied after the text match, so a very selective filter can leave
fewer than `k` results; OVERFETCH makes that rare.
"""

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import TS_CONFIG, EmbeddingStatus, Item, search_vector
from app.services.recommendation.filter_builder import matches_filter

# Text matches fetched per result wanted, before filters.
OVERFETCH = 10
MAX_FETCH = 500
_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class KeywordMatch:
    external_id: str
    pinecone_id: str
    score: float
    metadata: dict[str, Any]


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class KeywordSearch:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        tenant_id: uuid.UUID,
        text: str,
        pinecone_filter: dict[str, Any],
        k: int,
    ) -> list[KeywordMatch]:
        """The `k` best text matches among the workspace's embedded items, best first."""
        if not words(text):
            return []
        fetch = min(k * OVERFETCH, MAX_FETCH) if pinecone_filter else k
        if self._session.get_bind().dialect.name == "postgresql":
            matches = await self._postgres(tenant_id, text, fetch)
        else:
            matches = await self._in_python(tenant_id, text, fetch)
        return [m for m in matches if matches_filter(m.metadata, pinecone_filter)][:k]

    async def _postgres(self, tenant_id: uuid.UUID, text: str, limit: int) -> list[KeywordMatch]:
        # plainto_tsquery ANDs the words; OR them so an item matching some still counts.
        anded = cast(func.plainto_tsquery(literal_column(f"'{TS_CONFIG}'"), text), String)
        query = cast(func.replace(anded, "&", "|"), TSQUERY)
        vector = search_vector(Item.search_text)
        rank = func.ts_rank_cd(vector, query)
        rows = await self._session.execute(
            select(Item.external_id, Item.pinecone_id, Item.item_metadata, rank)
            .where(
                Item.tenant_id == tenant_id,
                Item.embedding_status == EmbeddingStatus.DONE,
                Item.pinecone_id.is_not(None),
                vector.op("@@")(query),
            )
            .order_by(rank.desc(), Item.external_id)
            .limit(limit)
        )
        return [
            KeywordMatch(external_id, pinecone_id, float(score), metadata or {})
            for external_id, pinecone_id, metadata, score in rows.tuples()
        ]

    async def _in_python(self, tenant_id: uuid.UUID, text: str, limit: int) -> list[KeywordMatch]:
        query = set(words(text))
        rows = await self._session.execute(
            select(Item.external_id, Item.pinecone_id, Item.item_metadata, Item.search_text).where(
                Item.tenant_id == tenant_id,
                Item.embedding_status == EmbeddingStatus.DONE,
                Item.pinecone_id.is_not(None),
                Item.search_text.is_not(None),
            )
        )
        matches = []
        for external_id, pinecone_id, metadata, search_text in rows.tuples():
            counts = Counter(words(search_text or ""))
            hits = [counts[w] for w in query if counts[w]]
            if hits:
                # Distinct words matched dominate; repeats add a little, damped by length.
                score = len(hits) + sum(hits) / (10 * math.sqrt(sum(counts.values())))
                matches.append(KeywordMatch(external_id, pinecone_id, score, metadata or {}))
        matches.sort(key=lambda m: (-m.score, m.external_id))
        return matches[:limit]
