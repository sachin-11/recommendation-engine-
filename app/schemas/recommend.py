"""Recommendation, feedback and analytics schemas."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.item import EmbeddingStatus
from app.models.recommendation_log import QueryType
from app.models.token_usage import UsageSource
from app.models.user_feedback import FeedbackType

MAX_TOP_K = 100
MAX_BATCH_QUERIES = 20

QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
TopK = Annotated[int, Field(ge=1, le=MAX_TOP_K)]
Filters = Annotated[
    dict[str, Any],
    Field(
        description=(
            "Keys must be the tenant's filter_fields. Value forms: 'Delhi' (exact), "
            "['Delhi', 'Pune'] (any of), {'gte': 3, 'lte': 8} "
            "(range; also gt, lt, eq, ne, in, nin)."
        ),
    ),
]


class _RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: TopK = 10
    filters: Filters = Field(default_factory=dict)
    include_raw_data: bool = Field(
        default=False, description="Include each item's full uploaded data (never cached)."
    )


class TextRecommendRequest(_RecommendRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "senior python developer with fastapi experience",
                "top_k": 10,
                "filters": {"location": "Delhi"},
                "include_raw_data": False,
            }
        }
    )

    query: QueryText


class ItemRecommendRequest(_RecommendRequest):
    model_config = ConfigDict(
        json_schema_extra={"example": {"external_id": "job_456", "top_k": 10, "filters": {}}}
    )

    external_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]


class ProfileRecommendRequest(_RecommendRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "profile": {
                    "skills": "Python, FastAPI, PostgreSQL",
                    "experience": "5 years backend development",
                    "preferred_location": "Remote",
                },
                "top_k": 10,
                "filters": {},
            }
        }
    )

    profile: dict[str, Any] = Field(min_length=1, max_length=50)


class RecommendationOut(BaseModel):
    rank: int
    external_id: str
    score: float
    score_label: str
    metadata: dict[str, Any]
    raw_data: dict[str, Any] | None = Field(
        default=None, description="Present only when include_raw_data is true."
    )


class RecommendResponse(BaseModel):
    results: list[RecommendationOut]
    total: int
    query_id: uuid.UUID = Field(description="Send this back with feedback on a result.")
    latency_ms: int
    embedding_tokens: int = Field(
        description="OpenAI tokens used to embed this query (0 when served from cache)."
    )
    request_id: str


class BatchQueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    query: QueryText
    filters: Filters = Field(default_factory=dict)


class BatchRecommendRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "queries": [
                    {"id": "q1", "query": "python developer"},
                    {"id": "q2", "query": "react frontend engineer"},
                ],
                "top_k": 5,
            }
        },
    )

    queries: list[BatchQueryIn] = Field(min_length=1, max_length=MAX_BATCH_QUERIES)
    top_k: TopK = 10

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [q.id for q in self.queries]
        if len(set(ids)) != len(ids):
            raise ValueError("Query ids must be unique")
        return self


class BatchRecommendResponse(BaseModel):
    results: dict[str, list[RecommendationOut]]
    query_ids: dict[str, uuid.UUID] = Field(description="Per query, for sending feedback.")
    latency_ms: int
    embedding_tokens: int = Field(description="OpenAI tokens used for the whole batch.")
    request_id: str


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: uuid.UUID
    external_item_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    feedback_type: FeedbackType


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query_id: uuid.UUID = Field(validation_alias="recommendation_log_id")
    external_item_id: str
    feedback_type: FeedbackType
    created_at: datetime


# --- Analytics ---


class ItemCount(BaseModel):
    external_id: str
    count: int


class AnalyticsPeriod(BaseModel):
    today_since: datetime
    month_since: datetime


class AnalyticsOverview(BaseModel):
    total_items: int
    total_recommendations_today: int
    total_recommendations_this_month: int
    avg_latency_ms: int | None = Field(description="This month; null when there were no queries.")
    top_queried_items: list[ItemCount] = Field(
        description="Items most often used as the input of a by-item query this month."
    )
    top_recommended_items: list[ItemCount] = Field(
        description="Items most often returned as the top result this month."
    )
    embedding_status_breakdown: dict[EmbeddingStatus, int]
    period: AnalyticsPeriod


class DailyUsage(BaseModel):
    date: str = Field(description="UTC date, YYYY-MM-DD.")
    count: int
    avg_latency_ms: int | None


class UsageResponse(BaseModel):
    days: int
    since: datetime
    total_recommendations: int
    daily: list[DailyUsage]
    by_query_type: dict[QueryType, int]
    cache_hit_rate: float | None = Field(
        description="Share of cacheable queries served from cache; null with no queries."
    )
    feedback_total: int


class DailyTokens(BaseModel):
    date: str = Field(description="UTC date, YYYY-MM-DD.")
    ingest_tokens: int
    query_tokens: int


class TokenUsageResponse(BaseModel):
    days: int
    since: datetime
    model: str
    total_tokens: int
    by_source: dict[UsageSource, int] = Field(
        description="INGEST: embedding uploaded items. QUERY: embedding recommendation queries."
    )
    api_calls: int = Field(description="Requests made to the OpenAI embeddings API.")
    texts_embedded: int = Field(description="Texts sent for embedding, including cache hits.")
    cache_hits: int = Field(description="Texts served from the embedding cache (no tokens).")
    price_per_million_tokens: float = Field(description="USD, from server configuration.")
    estimated_cost_usd: float
    daily: list[DailyTokens]


class FeedbackSummary(BaseModel):
    since: datetime
    days: int
    total: int
    by_type: dict[FeedbackType, int]
