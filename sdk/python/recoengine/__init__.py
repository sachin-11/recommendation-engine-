"""Official Python client for the RecoEngine recommendation API.

from recoengine import RecoEngineClient

client = RecoEngineClient(api_key="reco_…")
results = client.recommend.by_text("senior python developer", top_k=10)
"""

from recoengine._version import __version__
from recoengine.client import AsyncRecoEngineClient, RecoEngineClient
from recoengine.exceptions import (
    APIConnectionError,
    AuthError,
    NotFoundError,
    RateLimitError,
    RecoEngineError,
    ServiceUnavailableError,
    ValidationError,
)
from recoengine.models import (
    BatchQuery,
    BatchRecommendResult,
    BatchResult,
    BatchStatus,
    CsvBatchResult,
    FeedbackSummary,
    Item,
    OverviewStats,
    PaginatedItems,
    Recommendation,
    RecommendResult,
    SyncUploadResult,
    TokenUsage,
    UploadResult,
    UsageStats,
)

__all__ = [
    "APIConnectionError",
    "AsyncRecoEngineClient",
    "AuthError",
    "BatchQuery",
    "BatchRecommendResult",
    "BatchResult",
    "BatchStatus",
    "CsvBatchResult",
    "FeedbackSummary",
    "Item",
    "NotFoundError",
    "OverviewStats",
    "PaginatedItems",
    "RateLimitError",
    "RecoEngineClient",
    "RecoEngineError",
    "RecommendResult",
    "Recommendation",
    "ServiceUnavailableError",
    "SyncUploadResult",
    "TokenUsage",
    "UploadResult",
    "UsageStats",
    "ValidationError",
    "__version__",
]
