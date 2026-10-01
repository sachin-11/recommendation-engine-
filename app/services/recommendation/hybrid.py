"""Fuses vector-search and keyword-search results into one candidate list.

    retrieval_score = cosine similarity + w * keyword score / best keyword score

w is `domain_config.ranking.keyword`. An item found by both gets both parts; an exact
keyword match that vector search ranked low (a product code, a library name) moves up
by up to w. Keeping the cosine as the base keeps the score on the similarity scale the
reranker adds its feedback adjustments to. Reciprocal rank fusion, the other common
choice, would replace it with tiny rank-based numbers.

`score` stays the cosine similarity: keyword-only items get theirs from their stored
vector, so every result's score means the same thing.
"""

import math
from collections.abc import Sequence
from typing import Any

from app.services.recommendation.keyword_search import KeywordMatch

Matches = list[dict[str, Any]]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def keyword_only_ids(vector_matches: Matches, keyword_matches: list[KeywordMatch]) -> list[str]:
    """Pinecone ids of keyword hits that vector search did not return: their vectors are
    needed to give them a similarity score."""
    found = {m["id"] for m in vector_matches}
    return [k.pinecone_id for k in keyword_matches if k.pinecone_id not in found]


def fuse(
    vector_matches: Matches,
    keyword_matches: list[KeywordMatch],
    keyword_vectors: dict[str, list[float]],
    query_vector: Sequence[float],
    weight: float,
    k: int,
) -> Matches:
    """The best `k` of both lists by retrieval score, as Pinecone-style match dicts with
    `retrieval_score` set. Keyword hits whose vector is gone are dropped."""
    best = max((m.score for m in keyword_matches), default=0.0)
    boost = {m.pinecone_id: m.score / best for m in keyword_matches} if best > 0 else {}

    merged: dict[str, dict[str, Any]] = {m["id"]: dict(m) for m in vector_matches}
    for match in keyword_matches:
        if match.pinecone_id in merged:
            continue
        vector = keyword_vectors.get(match.pinecone_id)
        if vector is None:
            continue
        merged[match.pinecone_id] = {
            "id": match.pinecone_id,
            "score": cosine(query_vector, vector),
            "metadata": {**match.metadata, "external_id": match.external_id},
        }
    for pinecone_id, candidate in merged.items():
        candidate["retrieval_score"] = candidate["score"] + weight * boost.get(pinecone_id, 0.0)
    ranked = sorted(merged.values(), key=lambda m: m["retrieval_score"], reverse=True)
    return ranked[:k]
