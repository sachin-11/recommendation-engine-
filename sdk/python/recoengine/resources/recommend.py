"""Recommendations and feedback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

import httpx

from recoengine._base import recommend_body
from recoengine.models import BatchQuery, BatchRecommendResult, FeedbackType, RecommendResult

if TYPE_CHECKING:
    from recoengine.client import AsyncRecoEngineClient, RecoEngineClient

Filters = Optional[Dict[str, Any]]
BatchQueries = Sequence[Union[BatchQuery, Dict[str, Any]]]


def _result(response: httpx.Response) -> RecommendResult:
    return RecommendResult.model_validate(
        {**response.json(), "cache": response.headers.get("x-cache")}
    )


def _batch_body(queries: BatchQueries, top_k: int, user_id: Optional[str]) -> dict[str, Any]:
    items: List[Dict[str, Any]] = [
        q.model_dump() if isinstance(q, BatchQuery) else dict(q) for q in queries
    ]
    body: Dict[str, Any] = {"queries": items, "top_k": top_k}
    if user_id is not None:
        body["user_id"] = user_id
    return body


def _feedback_body(query_id: str, item_id: str, feedback_type: FeedbackType) -> dict[str, Any]:
    return {"query_id": query_id, "external_item_id": item_id, "feedback_type": feedback_type}


class Recommend:
    def __init__(self, client: RecoEngineClient) -> None:
        self._client = client

    def by_text(
        self,
        query: str,
        *,
        top_k: int = 10,
        filters: Filters = None,
        include_raw_data: bool = False,
        user_id: Optional[str] = None,
    ) -> RecommendResult:
        """Items most similar to free text.

        Filters use your `filter_fields`: `"Delhi"` exact, `["Delhi", "Pune"]` any of,
        `{"gte": 3, "lte": 8}` range. `user_id` is your id for the person who will see the
        results; feedback on them is attributed to that user.
        """
        body = {"query": query, **recommend_body(top_k, filters, include_raw_data, user_id)}
        return _result(self._client._request("POST", "/recommend/by-text", json=body))

    def by_item(
        self,
        external_id: str,
        *,
        top_k: int = 10,
        filters: Filters = None,
        include_raw_data: bool = False,
        user_id: Optional[str] = None,
    ) -> RecommendResult:
        """Items similar to one of your items; the item itself is never returned."""
        body = {
            "external_id": external_id,
            **recommend_body(top_k, filters, include_raw_data, user_id),
        }
        return _result(self._client._request("POST", "/recommend/by-item", json=body))

    def by_profile(
        self,
        profile: Dict[str, str],
        *,
        top_k: int = 10,
        filters: Filters = None,
        include_raw_data: bool = False,
        user_id: Optional[str] = None,
    ) -> RecommendResult:
        """Items matching a profile, e.g. {"skills": "Python", "experience": "5 years"}."""
        body = {"profile": profile, **recommend_body(top_k, filters, include_raw_data, user_id)}
        return _result(self._client._request("POST", "/recommend/by-profile", json=body))

    def batch(
        self, queries: BatchQueries, *, top_k: int = 10, user_id: Optional[str] = None
    ) -> BatchRecommendResult:
        """Up to 20 text queries in one request."""
        response = self._client._request(
            "POST", "/recommend/batch", json=_batch_body(queries, top_k, user_id)
        )
        return BatchRecommendResult.model_validate(
            {**response.json(), "cache": response.headers.get("x-cache")}
        )

    def submit_feedback(self, query_id: str, item_id: str, feedback_type: FeedbackType) -> None:
        """Record a user's reaction to a result, using the result's `query_id`."""
        self._client._request(
            "POST", "/recommend/feedback", json=_feedback_body(query_id, item_id, feedback_type)
        )


class AsyncRecommend:
    def __init__(self, client: AsyncRecoEngineClient) -> None:
        self._client = client

    async def by_text(
        self,
        query: str,
        *,
        top_k: int = 10,
        filters: Filters = None,
        include_raw_data: bool = False,
        user_id: Optional[str] = None,
    ) -> RecommendResult:
        body = {"query": query, **recommend_body(top_k, filters, include_raw_data, user_id)}
        return _result(await self._client._request("POST", "/recommend/by-text", json=body))

    async def by_item(
        self,
        external_id: str,
        *,
        top_k: int = 10,
        filters: Filters = None,
        include_raw_data: bool = False,
        user_id: Optional[str] = None,
    ) -> RecommendResult:
        body = {
            "external_id": external_id,
            **recommend_body(top_k, filters, include_raw_data, user_id),
        }
        return _result(await self._client._request("POST", "/recommend/by-item", json=body))

    async def by_profile(
        self,
        profile: Dict[str, str],
        *,
        top_k: int = 10,
        filters: Filters = None,
        include_raw_data: bool = False,
        user_id: Optional[str] = None,
    ) -> RecommendResult:
        body = {"profile": profile, **recommend_body(top_k, filters, include_raw_data, user_id)}
        return _result(await self._client._request("POST", "/recommend/by-profile", json=body))

    async def batch(
        self, queries: BatchQueries, *, top_k: int = 10, user_id: Optional[str] = None
    ) -> BatchRecommendResult:
        response = await self._client._request(
            "POST", "/recommend/batch", json=_batch_body(queries, top_k, user_id)
        )
        return BatchRecommendResult.model_validate(
            {**response.json(), "cache": response.headers.get("x-cache")}
        )

    async def submit_feedback(
        self, query_id: str, item_id: str, feedback_type: FeedbackType
    ) -> None:
        await self._client._request(
            "POST", "/recommend/feedback", json=_feedback_body(query_id, item_id, feedback_type)
        )
