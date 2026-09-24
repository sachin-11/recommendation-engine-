"""Import every model here so `Base.metadata` is complete for Alembic and tests."""

from app.models.api_key import ApiKey
from app.models.base import Base, BaseEntity
from app.models.item import EmbeddingStatus, Item
from app.models.item_batch import BatchStatus, ItemBatch
from app.models.recommendation_log import QueryType, RecommendationLog
from app.models.tenant import DomainType, Tenant
from app.models.user_feedback import FeedbackType, UserFeedback

__all__ = [
    "ApiKey",
    "Base",
    "BaseEntity",
    "BatchStatus",
    "DomainType",
    "EmbeddingStatus",
    "FeedbackType",
    "Item",
    "ItemBatch",
    "QueryType",
    "RecommendationLog",
    "Tenant",
    "UserFeedback",
]
