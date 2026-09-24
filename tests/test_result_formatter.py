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
        (0.66, "Excellent Match"),
        (0.65, "Good Match"),
        (0.51, "Good Match"),
        (0.50, "Fair Match"),
        (0.36, "Fair Match"),
        (0.35, "Weak Match"),
        (0.10, "Weak Match"),
    ],
)
def test_default_score_labels(score: float, label: str) -> None:
    assert score_label(score) == label


@pytest.mark.parametrize(
    ("score", "expected"),
    # Real scores from the docs' domain guides (text-embedding-3-small).
    [
        (0.7108, "Excellent Match"),  # "senior python backend engineer with fastapi" -> job-101
        (0.5410, "Good Match"),  # "spicy vegetarian pasta" -> Penne Arrabbiata
        (0.4626, "Fair Match"),  # "spicy vegetarian pasta" -> Chilli Garlic Noodles
        (0.2633, "Weak Match"),  # "light healthy breakfast" -> Paneer Tikka
    ],
)
def test_labels_on_real_scores(score: float, expected: str) -> None:
    assert score_label(score) == expected


def test_custom_thresholds() -> None:
    assert score_label(0.8, (0.9, 0.7, 0.5)) == "Good Match"
    assert score_label(0.4, (0.9, 0.7, 0.5)) == "Weak Match"


def test_thresholds_are_configurable_and_validated() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    base = {
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "REDIS_URL": "redis://x",
        "SECRET_KEY": "k" * 40,
    }
    parsed = Settings(**base, SCORE_LABEL_THRESHOLDS="0.8,0.6,0.4")  # type: ignore[arg-type]
    assert parsed.SCORE_LABEL_THRESHOLDS == (0.8, 0.6, 0.4)
    with pytest.raises(ValidationError):
        Settings(**base, SCORE_LABEL_THRESHOLDS="0.4,0.6,0.8")  # type: ignore[arg-type]


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
