import pytest

from app.models import Tenant
from app.services.recommendation.result_formatter import ResultFormatter, score_label

TENANT = Tenant(
    name="Acme",
    email="acme@example.com",
    domain_type="HR",
    domain_config={"filter_fields": ["location", "job_type"]},
)

formatter = ResultFormatter()


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (0.99, "Excellent Match"),
        (0.86, "Excellent Match"),
        (0.85, "Good Match"),
        (0.71, "Good Match"),
        (0.70, "Fair Match"),
        (0.51, "Fair Match"),
        (0.50, "Weak Match"),
        (0.10, "Weak Match"),
    ],
)
def test_score_labels(score: float, label: str) -> None:
    assert score_label(score) == label


def test_results_are_ranked_by_score() -> None:
    matches = [
        {"id": "v1", "score": 0.61, "metadata": {"external_id": "job-1"}},
        {"id": "v2", "score": 0.923456, "metadata": {"external_id": "job-2"}},
        {"id": "v3", "score": 0.75, "metadata": {"external_id": "job-3"}},
    ]

    results = formatter.format_results(matches, TENANT)

    assert [(r["rank"], r["external_id"]) for r in results] == [
        (1, "job-2"),
        (2, "job-3"),
        (3, "job-1"),
    ]
    assert results[0]["score"] == 0.9235
    assert results[0]["score_label"] == "Excellent Match"


def test_metadata_keeps_only_filter_fields() -> None:
    match = {
        "id": "v1",
        "score": 0.8,
        "metadata": {
            "external_id": "job-1",
            "tenant_id": "t",
            "location": "Delhi",
            "job_type": "full_time",
            "legacy": "x",
        },
    }

    [result] = formatter.format_results([match], TENANT)

    assert result["metadata"] == {"location": "Delhi", "job_type": "full_time"}
    assert "raw_data" not in result


def test_raw_data_only_when_requested() -> None:
    matches = [
        {"id": "v1", "score": 0.8, "metadata": {"external_id": "job-1"}},
        {"id": "v2", "score": 0.7, "metadata": {"external_id": "gone"}},
    ]

    results = formatter.format_results(
        matches, TENANT, include_raw_data=True, raw_data={"job-1": {"title": "Engineer"}}
    )

    assert results[0]["raw_data"] == {"title": "Engineer"}
    assert results[1]["raw_data"] is None


def test_falls_back_to_vector_id_without_external_id() -> None:
    [result] = formatter.format_results([{"id": "v9", "score": 0.3, "metadata": None}], TENANT)
    assert result["external_id"] == "v9"
    assert result["metadata"] == {}


def test_no_matches() -> None:
    assert formatter.format_results([], TENANT) == []
