"""Import every model here so `Base.metadata` is complete for Alembic and tests."""

from app.models.api_key import ApiKey
from app.models.base import Base, BaseEntity
from app.models.item import EmbeddingStatus, Item
from app.models.item_batch import BatchStatus, ItemBatch
from app.models.tenant import DomainType, Tenant

__all__ = [
    "ApiKey",
    "Base",
    "BaseEntity",
    "BatchStatus",
    "DomainType",
    "EmbeddingStatus",
    "Item",
    "ItemBatch",
    "Tenant",
]
