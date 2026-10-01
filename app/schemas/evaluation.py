"""Offline evaluation: golden-set queries and comparison runs."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.tenant import RankingConfig, Weight

MAX_EVAL_QUERIES = 200
MAX_RELEVANT = 100
MAX_VARIANTS = 5

Grade = Annotated[int, Field(ge=1, le=3)]
ExternalId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Fraction = Annotated[float, Field(ge=0, le=1)]


class EvalQueryIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "query": "senior python developer with fastapi",
                "relevant": {"job_101": 3, "job_245": 2, "job_310": 1},
            }
        },
    )

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    relevant: dict[ExternalId, Grade] = Field(
        min_length=1,
        max_length=MAX_RELEVANT,
        description="Items a good answer contains, graded 1 (relevant) to 3 (perfect).",
    )


class EvalQueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query: str
    relevant: dict[str, int]
    created_at: datetime
    updated_at: datetime


class RankingOverride(BaseModel):
    """Ranking settings to evaluate; omitted fields keep the workspace's saved values."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    engagement: Weight | None = None
    conversion: Weight | None = None
    negative: Weight | None = None
    popularity: Weight | None = None
    personalization: Fraction | None = None
    keyword: Fraction | None = None


class EvalVariantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    ranking: RankingOverride = Field(default_factory=RankingOverride)


class EvalRunRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "k": 10,
                "variants": [
                    {"name": "vector", "ranking": {"enabled": False}},
                    {"name": "hybrid 0.3", "ranking": {"keyword": 0.3}},
                ],
            }
        },
    )

    k: int = Field(default=10, ge=1, le=50, description="Metrics count the top k results.")
    variants: list[EvalVariantIn] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_VARIANTS,
        description="Settings to compare. Default: vector only, hybrid, and your saved settings.",
    )


class Metrics(BaseModel):
    ndcg: float = Field(description="NDCG@k: 1 is the best possible order.")
    recall: float = Field(description="Recall@k: share of the relevant items found.")
    mrr: float = Field(description="Mean reciprocal rank of the first relevant result.")


class EvalVariantResult(Metrics):
    name: str
    ranking: RankingConfig


class EvalQueryResult(BaseModel):
    id: uuid.UUID
    query: str
    relevant: dict[str, int]
    by_variant: dict[str, Metrics]
    top: dict[str, list[str]] = Field(description="Per variant, the external ids returned.")


class EvalRunResponse(BaseModel):
    k: int
    queries: int
    variants: list[EvalVariantResult]
    per_query: list[EvalQueryResult]
