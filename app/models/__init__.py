"""Import every model here so `Base.metadata` is complete for Alembic and tests."""

from app.models.api_key import ApiKey
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.base import Base, BaseEntity
from app.models.eval_query import EvalQuery
from app.models.invitation import Invitation
from app.models.item import EmbeddingStatus, Item
from app.models.item_batch import BatchStatus, ItemBatch
from app.models.item_stats import ItemStats
from app.models.recommendation_impression import RecommendationImpression
from app.models.recommendation_log import QueryType, RankingVariant, RecommendationLog
from app.models.stripe_event import StripeEvent
from app.models.tenant import DomainType, Tenant
from app.models.token_usage import TokenUsage, UsageSource
from app.models.user import Role, User
from app.models.user_feedback import FeedbackType, UserFeedback

__all__ = [
    "ApiKey",
    "AuthToken",
    "AuthTokenPurpose",
    "Base",
    "BaseEntity",
    "BatchStatus",
    "DomainType",
    "EmbeddingStatus",
    "EvalQuery",
    "FeedbackType",
    "Invitation",
    "Item",
    "ItemBatch",
    "ItemStats",
    "QueryType",
    "RankingVariant",
    "RecommendationImpression",
    "RecommendationLog",
    "Role",
    "StripeEvent",
    "Tenant",
    "TokenUsage",
    "UsageSource",
    "User",
    "UserFeedback",
]
