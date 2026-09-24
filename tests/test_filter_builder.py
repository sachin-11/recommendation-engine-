from typing import Any

import pytest

from app.core.exceptions import BadRequestError
from app.services.recommendation.filter_builder import FilterBuilder

CONFIG: dict[str, Any] = {
    "primary_embedding_field": "description",
    "searchable_fields": ["title", "description"],
    "filter_fields": ["location", "experience_years", "job_type", "remote"],
}

builder = FilterBuilder()


def build(filters: dict[str, Any] | None) -> dict[str, Any]:
    return builder.build_pinecone_filter(filters, CONFIG)


def test_empty_filters() -> None:
    assert build(None) == {}
    assert build({}) == {}


def test_exact_match_for_scalars() -> None:
    assert build({"location": "Delhi", "remote": True, "experience_years": 3}) == {
        "location": {"$eq": "Delhi"},
        "remote": {"$eq": True},
        "experience_years": {"$eq": 3},
    }


def test_list_means_any_of() -> None:
    assert build({"job_type": ["full_time", "contract"]}) == {
        "job_type": {"$in": ["full_time", "contract"]}
    }


def test_numeric_range() -> None:
    assert build({"experience_years": {"gte": 3, "lt": 8}}) == {
        "experience_years": {"$gte": 3, "$lt": 8}
    }


def test_operators_with_dollar_prefix_are_accepted() -> None:
    assert build({"location": {"$ne": "Delhi"}, "job_type": {"$nin": ["intern"]}}) == {
        "location": {"$ne": "Delhi"},
        "job_type": {"$nin": ["intern"]},
    }


def test_the_docs_example() -> None:
    assert build({"location": "Delhi", "experience_years": {"gte": 3}}) == {
        "location": {"$eq": "Delhi"},
        "experience_years": {"$gte": 3},
    }


def test_unknown_field_lists_allowed_fields() -> None:
    with pytest.raises(BadRequestError) as exc_info:
        build({"salary": 100})
    [detail] = exc_info.value.details or []
    assert detail["field"] == "salary"
    assert detail["type"] == "unknown_filter"
    assert "location, experience_years, job_type, remote" in detail["message"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"gte": "three"}, "needs a number"),
        ({"gte": True}, "needs a number"),
        ({"between": [1, 2]}, "Unknown operator"),
        ({}, "empty"),
        ({"in": "Delhi"}, "needs a list"),
        ({"eq": ["a"]}, "single value"),
        ([], "must not be empty"),
        ([{"a": 1}], "strings, numbers or booleans"),
        ({"gte": 10, "lte": 2}, "Empty range"),
        (None, "Use a value"),
    ],
)
def test_invalid_values(value: Any, message: str) -> None:
    with pytest.raises(BadRequestError) as exc_info:
        build({"experience_years": value})
    [detail] = exc_info.value.details or []
    assert message in detail["message"]


def test_all_errors_are_reported_together() -> None:
    with pytest.raises(BadRequestError) as exc_info:
        build({"salary": 1, "experience_years": {"gte": "x"}})
    assert {d["field"] for d in exc_info.value.details or []} == {"salary", "experience_years"}
