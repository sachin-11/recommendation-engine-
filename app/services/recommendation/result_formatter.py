"""Shapes raw Pinecone matches into the ranked results returned by the API."""

from typing import Any

from app.models.tenant import Tenant

# (exclusive lower bound, label), checked in order. Cosine similarity of
# text-embedding-3 models rarely exceeds ~0.7 even for close matches, so tune these
# against real queries before showing labels to end users.
SCORE_LABELS: list[tuple[float, str]] = [
    (0.85, "Excellent Match"),
    (0.70, "Good Match"),
    (0.50, "Fair Match"),
]
LOWEST_LABEL = "Weak Match"


def score_label(score: float) -> str:
    for threshold, label in SCORE_LABELS:
        if score > threshold:
            return label
    return LOWEST_LABEL


class ResultFormatter:
    def format_results(
        self,
        pinecone_matches: list[dict[str, Any]],
        tenant: Tenant,
        include_raw_data: bool = False,
        raw_data: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Rank matches best first. `metadata` keeps only the tenant's filter fields;
        `raw_data` (looked up by external_id) is added only when asked for."""
        filter_fields = set(tenant.domain_config.get("filter_fields") or [])
        ordered = sorted(pinecone_matches, key=lambda m: m["score"], reverse=True)
        results: list[dict[str, Any]] = []
        for rank, match in enumerate(ordered, start=1):
            metadata = match.get("metadata") or {}
            external_id = str(metadata.get("external_id") or match["id"])
            result: dict[str, Any] = {
                "rank": rank,
                "external_id": external_id,
                "score": round(match["score"], 4),
                "score_label": score_label(match["score"]),
                "metadata": {k: v for k, v in metadata.items() if k in filter_fields},
            }
            if include_raw_data:
                result["raw_data"] = (raw_data or {}).get(external_id)
            results.append(result)
        return results
