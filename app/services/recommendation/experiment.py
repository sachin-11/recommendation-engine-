"""Splits traffic between ranking variants for an A/B test, and the statistics to judge it.

With `domain_config.ranking.control_share` above 0, that share of traffic is served
CONTROL (similarity alone) and the rest RERANKED (feedback and personalization). A
request with a `user_id` is assigned by a hash of workspace and user, so a person sees
one variant consistently; anonymous requests are assigned at random per query.
"""

import hashlib
import math
import random
import uuid

from app.models.recommendation_log import RankingVariant
from app.schemas.tenant import RankingConfig

# z for a two-sided 95% interval.
Z_95 = 1.959964


def _bucket(tenant_id: uuid.UUID, user_id: str) -> float:
    """A stable number in [0, 1) for this user in this workspace."""
    digest = hashlib.sha256(f"{tenant_id}:{user_id}".encode()).hexdigest()
    return int(digest[:15], 16) / 16**15


def assign_variant(
    config: RankingConfig, tenant_id: uuid.UUID, user_id: str | None
) -> RankingVariant:
    if not config.enabled or config.control_share >= 1:
        return RankingVariant.CONTROL
    if config.control_share <= 0:
        return RankingVariant.RERANKED
    bucket = _bucket(tenant_id, user_id) if user_id else random.random()
    return RankingVariant.CONTROL if bucket < config.control_share else RankingVariant.RERANKED


def wilson_interval(successes: float, trials: float) -> tuple[float, float] | None:
    """95% confidence interval for a rate; better than rate ± 2 SE for small counts."""
    if trials <= 0:
        return None
    p = min(successes, trials) / trials
    z2 = Z_95**2
    centre = (p + z2 / (2 * trials)) / (1 + z2 / trials)
    half = Z_95 * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials**2)) / (1 + z2 / trials)
    return max(0.0, centre - half), min(1.0, centre + half)


def two_proportion_p_value(s1: float, n1: float, s2: float, n2: float) -> float | None:
    """Two-sided p-value that two rates differ (pooled z-test). None without data."""
    if n1 <= 0 or n2 <= 0:
        return None
    s1, s2 = min(s1, n1), min(s2, n2)
    pooled = (s1 + s2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = abs(s1 / n1 - s2 / n2) / se
    return math.erfc(z / math.sqrt(2))
