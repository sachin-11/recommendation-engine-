"""Usage analytics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from recoengine.models import FeedbackSummary, OverviewStats, UsageStats

if TYPE_CHECKING:
    from recoengine.client import AsyncRecoEngineClient, RecoEngineClient


class Analytics:
    def __init__(self, client: RecoEngineClient) -> None:
        self._client = client

    def overview(self) -> OverviewStats:
        """Item count, query volume today and this month, average latency, top items."""
        return OverviewStats.model_validate(
            self._client._request("GET", "/analytics/overview").json()
        )

    def feedback_summary(self, *, days: int = 30) -> FeedbackSummary:
        response = self._client._request(
            "GET", "/analytics/feedback-summary", params={"days": days}
        )
        return FeedbackSummary.model_validate(response.json())

    def usage(self, *, days: int = 30) -> UsageStats:
        response = self._client._request("GET", "/analytics/usage", params={"days": days})
        return UsageStats.model_validate(response.json())


class AsyncAnalytics:
    def __init__(self, client: AsyncRecoEngineClient) -> None:
        self._client = client

    async def overview(self) -> OverviewStats:
        response = await self._client._request("GET", "/analytics/overview")
        return OverviewStats.model_validate(response.json())

    async def feedback_summary(self, *, days: int = 30) -> FeedbackSummary:
        response = await self._client._request(
            "GET", "/analytics/feedback-summary", params={"days": days}
        )
        return FeedbackSummary.model_validate(response.json())

    async def usage(self, *, days: int = 30) -> UsageStats:
        response = await self._client._request("GET", "/analytics/usage", params={"days": days})
        return UsageStats.model_validate(response.json())
