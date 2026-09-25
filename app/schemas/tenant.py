"""Tenant, domain-config and API-key request/response schemas."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.security import API_KEY_PREFIX
from app.models.tenant import DomainType

# Field names become embedding inputs and vector-metadata keys, so keep them identifier-like.
FieldName = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
]
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _ensure_unique(values: list[str], field: str) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must not contain duplicates")
    return values


class DomainConfig(BaseModel):
    """Describes a tenant's item schema. This is what makes the engine domain-agnostic."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "primary_embedding_field": "description",
                "searchable_fields": ["title", "description", "tags"],
                "filter_fields": ["location", "category"],
                "item_label": "job",
            }
        },
    )

    primary_embedding_field: FieldName
    searchable_fields: list[FieldName] = Field(min_length=1, max_length=50)
    filter_fields: list[FieldName] = Field(default_factory=list, max_length=50)
    item_label: Annotated[NonBlankStr, StringConstraints(max_length=50)]

    @field_validator("searchable_fields")
    @classmethod
    def _unique_searchable(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "searchable_fields")

    @field_validator("filter_fields")
    @classmethod
    def _unique_filters(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "filter_fields")

    @model_validator(mode="after")
    def _primary_field_is_searchable(self) -> Self:
        if self.primary_embedding_field not in self.searchable_fields:
            raise ValueError("primary_embedding_field must be one of searchable_fields")
        return self


DEFAULT_DOMAIN_CONFIGS: dict[DomainType, DomainConfig] = {
    DomainType.HR: DomainConfig(
        primary_embedding_field="description",
        searchable_fields=["title", "description", "skills"],
        filter_fields=["location", "department", "employment_type"],
        item_label="job",
    ),
    DomainType.FOOD: DomainConfig(
        primary_embedding_field="description",
        searchable_fields=["name", "description", "cuisine", "ingredients"],
        filter_fields=["cuisine", "dietary_tags", "price_range"],
        item_label="dish",
    ),
    DomainType.ECOMMERCE: DomainConfig(
        primary_embedding_field="description",
        searchable_fields=["title", "description", "brand", "tags"],
        filter_fields=["category", "brand", "price_range"],
        item_label="product",
    ),
    DomainType.EDTECH: DomainConfig(
        primary_embedding_field="description",
        searchable_fields=["title", "description", "topics"],
        filter_fields=["level", "category", "language"],
        item_label="course",
    ),
}


# --- Tenants ---


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[NonBlankStr, StringConstraints(max_length=255)]
    email: EmailStr
    domain_type: DomainType
    domain_config: DomainConfig | None = Field(
        default=None,
        description=(
            "Optional for HR/FOOD/ECOMMERCE/EDTECH (a preset is used); required for CUSTOM."
        ),
    )

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def _custom_requires_config(self) -> Self:
        if self.domain_config is None and self.domain_type not in DEFAULT_DOMAIN_CONFIGS:
            raise ValueError(f"domain_config is required when domain_type is {self.domain_type}")
        return self

    def resolved_domain_config(self) -> DomainConfig:
        return self.domain_config or DEFAULT_DOMAIN_CONFIGS[self.domain_type]


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    domain_type: DomainType
    domain_config: DomainConfig
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- API keys ---


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[NonBlankStr, StringConstraints(max_length=100)] = Field(
        description="Human-readable label, e.g. 'production-backend'."
    )
    expires_at: AwareDatetime | None = Field(
        default=None, description="Optional expiry. The key is rejected after this time."
    )

    @field_validator("expires_at")
    @classmethod
    def _expiry_in_future(cls, value: datetime | None) -> datetime | None:
        if value is not None and value <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value


class ApiKeyResponse(BaseModel):
    """Safe representation of a key: never includes the plain key or its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    created_by_id: uuid.UUID | None = Field(
        default=None, description="The user who created the key, if they still exist."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_key(self) -> str:
        return f"{API_KEY_PREFIX}{self.key_prefix}..."


API_KEY_WARNING = "Save this key, it won't be shown again"


class ApiKeyCreatedResponse(ApiKeyResponse):
    api_key: str = Field(description="The plain API key. Returned only once, at creation.")
    warning: str = API_KEY_WARNING
