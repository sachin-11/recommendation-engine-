"""Ranking quality metrics for offline evaluation against a golden set.

`relevant` maps external ids to a grade: 1 relevant, 2 very relevant, 3 perfect.
`ranked` is what the engine returned, best first. All metrics are in [0, 1], higher is
better, and only the first `k` results count.
"""

import math
from collections.abc import Sequence


def recall_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    """Share of the relevant items found in the top k."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant.keys()) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    """1 / rank of the first relevant result; 0 when none is in the top k."""
    for rank, external_id in enumerate(ranked[:k], start=1):
        if external_id in relevant:
            return 1 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    """Normalised discounted cumulative gain: rewards putting the most relevant items
    first. 1 means the best possible order of the relevant items."""

    def dcg(grades: Sequence[int]) -> float:
        return float(sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades)))

    ideal = dcg(sorted(relevant.values(), reverse=True)[:k])
    if ideal == 0:
        return 0.0
    return dcg([relevant.get(external_id, 0) for external_id in ranked[:k]]) / ideal
