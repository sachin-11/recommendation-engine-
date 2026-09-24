"""Import every model here so `Base.metadata` is complete for Alembic and tests."""

from app.models.api_key import ApiKey
from app.models.base import Base, BaseEntity
from app.models.tenant import DomainType, Tenant

__all__ = ["ApiKey", "Base", "BaseEntity", "DomainType", "Tenant"]
