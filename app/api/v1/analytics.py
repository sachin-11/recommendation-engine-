"""Per-tenant usage analytics. All routes require an X-API-Key header."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.v1.items import AUTH_RESPONSES, PROTECTED
from app.schemas.recommend import (
    AnalyticsOverview,
    FeedbackSummary,
    TokenUsageResponse,
    UsageResponse,
)
from app.services.analytics_service import AnalyticsServiceDep

router = APIRouter(
    prefix="/analytics", tags=["analytics"], dependencies=PROTECTED, responses=AUTH_RESPONSES
)


@router.get("/overview", summary="Items, query volume, latency and top items")
async def overview(service: AnalyticsServiceDep) -> AnalyticsOverview:
    return AnalyticsOverview.model_validate(await service.overview())


@router.get("/feedback-summary", summary="Feedback counts by type")
async def feedback_summary(
    service: AnalyticsServiceDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> FeedbackSummary:
    return FeedbackSummary.model_validate(await service.feedback_summary(days))


@router.get("/usage", summary="Daily volume, query types and cache hit rate")
async def usage(
    service: AnalyticsServiceDep, days: Annotated[int, Query(ge=1, le=90)] = 30
) -> UsageResponse:
    return UsageResponse.model_validate(await service.usage(days))


@router.get("/tokens", summary="OpenAI embedding tokens and estimated cost")
async def tokens(
    service: AnalyticsServiceDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> TokenUsageResponse:
    return TokenUsageResponse.model_validate(await service.tokens(days))
