"""Pydantic models for API responses. Field names match the API reference.

Unknown fields are kept (extra="allow"), so newer API versions do not break older SDKs.
Typing uses Optional/List/Dict because Python 3.9 cannot evaluate `X | None` at runtime.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

EmbeddingStatus = Literal["PENDING", "PROCESSING", "DONE", "FAILED"]
BatchState = Literal["PENDING", "PROCESSING", "DONE", "PARTIAL_FAIL"]
QueryType = Literal["TEXT", "ITEM_ID", "PROFILE"]
FeedbackType = Literal["CLICK", "THUMBS_UP", "THUMBS_DOWN", "PURCHASE", "APPLY", "IGNORE"]
CacheStatus = Literal["HIT", "MISS", "BYPASS", "PARTIAL"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------- items


class Item(_Model):
    id: str
    external_id: str
    embedding_status: EmbeddingStatus
    pinecone_id: Optional[str] = None
    batch_id: Optional[str] = None
    raw_data: Dict[str, Any]
    #: Filter fields stored with the vector; `error` explains a FAILED status.
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PaginatedItems(_Model):
    items: List[Item]
    total: int
    page: int
    page_size: int
    pages: int


class BatchStatus(_Model):
    batch_id: str
    status: BatchState
    total_items: int
    processed_items: int
    failed_items: int
    progress_percentage: float
    created_at: datetime
    completed_at: Optional[datetime] = None

    @property
    def is_complete(self) -> bool:
        return self.status in ("DONE", "PARTIAL_FAIL")


class BatchResult(BatchStatus):
    mode: Literal["async"]
    status_url: str


class CsvBatchResult(BatchResult):
    #: CSV header -> item field it was stored as.
    column_mapping: Dict[str, str]


class ItemResult(_Model):
    external_id: str
    status: EmbeddingStatus
    error: Optional[str] = None


class SyncUploadResult(_Model):
    mode: Literal["sync"]
    total_items: int
    succeeded: int
    failed: int
    results: List[ItemResult]


UploadResult = Union[BatchResult, SyncUploadResult]
UPLOAD_RESULT: TypeAdapter[UploadResult] = TypeAdapter(
    Union[BatchResult, SyncUploadResult]  # discriminated by `mode`
)


class DeleteResult(_Model):
    deleted: int
    not_found: List[str]


# ---------------------------------------------------------------- recommendations


class Recommendation(_Model):
    rank: int
    external_id: str
    #: Cosine similarity, higher is closer.
    score: float
    score_label: str
    metadata: Dict[str, Any]
    raw_data: Optional[Dict[str, Any]] = None


class RecommendResult(_Model):
    results: List[Recommendation]
    total: int
    #: Pass to `recommend.submit_feedback`.
    query_id: str
    latency_ms: int
    #: OpenAI tokens used to embed the query; 0 on cache hits and item queries.
    embedding_tokens: int = 0
    request_id: str
    #: Value of the X-Cache response header.
    cache: Optional[CacheStatus] = None


class BatchRecommendResult(_Model):
    results: Dict[str, List[Recommendation]]
    query_ids: Dict[str, str]
    latency_ms: int
    #: OpenAI tokens used for the whole batch.
    embedding_tokens: int = 0
    request_id: str
    cache: Optional[CacheStatus] = None


# ---------------------------------------------------------------- analytics


class ItemCount(_Model):
    external_id: str
    count: int


class OverviewStats(_Model):
    total_items: int
    total_recommendations_today: int
    total_recommendations_this_month: int
    avg_latency_ms: Optional[int] = None
    top_queried_items: List[ItemCount]
    top_recommended_items: List[ItemCount]
    embedding_status_breakdown: Dict[str, int]


class FeedbackSummary(_Model):
    since: datetime
    days: int
    total: int
    by_type: Dict[str, int]


class DailyUsage(_Model):
    date: str
    count: int
    avg_latency_ms: Optional[int] = None


class UsageStats(_Model):
    days: int
    since: datetime
    total_recommendations: int
    daily: List[DailyUsage]
    by_query_type: Dict[str, int]
    cache_hit_rate: Optional[float] = None
    feedback_total: int


class DailyTokens(_Model):
    date: str
    ingest_tokens: int
    query_tokens: int


class TokenUsage(_Model):
    days: int
    since: datetime
    #: Embedding model the tokens were spent on.
    model: str
    total_tokens: int
    #: Tokens for uploads (``INGEST``) and queries (``QUERY``).
    by_source: Dict[str, int]
    api_calls: int
    texts_embedded: int
    #: Texts served from the embedding cache, which cost no tokens.
    cache_hits: int
    price_per_million_tokens: float
    estimated_cost_usd: float
    daily: List[DailyTokens]


class BatchQuery(_Model):
    id: str
    query: str
    filters: Dict[str, Any] = Field(default_factory=dict)
