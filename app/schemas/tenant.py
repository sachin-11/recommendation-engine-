"""Tenant, domain-config and API-key request/response schemas."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Self

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


Weight = Annotated[float, Field(ge=0, le=5)]


class RankingConfig(BaseModel):
    """How much user feedback reorders vector-search results. Each weight scales how far
    an item's rate is above or below the workspace average; similarity counts 1."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="False orders results by similarity to the query alone, as before.",
    )
    engagement: Weight = Field(default=0.5, description="Clicks and thumbs up per impression.")
    conversion: Weight = Field(default=0.5, description="Purchases and applies per impression.")
    negative: Weight = Field(default=0.5, description="Thumbs down and ignores per impression.")
    popularity: Weight = Field(
        default=0.0, description="How often the item is shown. Favours established items."
    )
    personalization: float = Field(
        default=0.2,
        ge=0,
        le=1,
        description=(
            "With a user_id, how far the query leans toward items that user liked: "
            "0 ignores their history, 1 matches on history alone."
        ),
    )
    keyword: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description=(
            "Hybrid search for text and profile queries: how much exact keyword matches "
            "lift an item, on top of its similarity. 0 is vector search alone."
        ),
    )
    llm_rerank: bool = Field(
        default=False,
        description=(
            "Text and profile queries: an OpenAI chat model reads the top results and "
            "reorders them, giving each a one-line reason. Adds about 1-3 s on a cache miss."
        ),
    )
    llm_candidates: int = Field(
        default=10, ge=2, le=20, description="How many top results the LLM reads."
    )
    control_share: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description=(
            "A/B test: the share of traffic served by similarity alone (the control), so "
            "feedback ranking can be compared against it. Users with a user_id always get "
            "the same variant. 0 runs no test."
        ),
    )


def ranking_config(domain_config: dict[str, Any]) -> RankingConfig:
    """The workspace's ranking settings; defaults for configs saved before they existed."""
    return RankingConfig.model_validate(domain_config.get("ranking") or {})


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
    ranking: RankingConfig = Field(default_factory=RankingConfig)

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
