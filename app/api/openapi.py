"""Public OpenAPI schema: tag names and descriptions, per-operation docs, examples.

Routes keep short internal tags; this module turns the generated schema into the one
published in the API reference (docs/static/openapi.json, exported by
scripts/export_openapi.py) and served at /openapi.json.
"""

from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

API_DESCRIPTION = """
RecoEngine turns any catalogue (jobs, dishes, products, courses, or your own items) into
recommendations. Upload items, and they are embedded with OpenAI and stored in a vector
index; then ask for items similar to a text, an existing item, or a profile.

**Authentication:** send your API key in the `X-API-Key` header. Keys are created at
registration and on the API Keys page of the dashboard.

**Errors** always look like `{"error": {"code": "...", "message": "...", "details": [...]}}`.
`429` and `503` responses include a `Retry-After` header (seconds).

**Rate limits:** 100 requests per minute per API key, 10,000 uploaded items per tenant per
UTC day.
""".strip()

TAGS: list[dict[str, str]] = [
    {"name": "Auth", "description": "Sign up and dashboard sessions."},
    {"name": "Account", "description": "The signed-in tenant: API keys, domain config, deletion."},
    {"name": "Items", "description": "Upload, list and delete the items you want to recommend."},
    {"name": "Recommendations", "description": "Similar items by text, item or profile; feedback."},
    {"name": "Analytics", "description": "Query volume, latency, cache hit rate, feedback."},
    {"name": "Index", "description": "Vector index statistics and rebuilds."},
    {
        "name": "Tenants",
        "description": "Tenant management by id. Prefer `/auth` and `/me` for new integrations.",
    },
    {"name": "Health", "description": "Liveness of the API and its dependencies."},
]

# Internal route tag -> published tag.
_TAG_NAMES = {
    "auth": "Auth",
    "account": "Account",
    "items": "Items",
    "recommend": "Recommendations",
    "analytics": "Analytics",
    "index": "Index",
    "tenants": "Tenants",
    "health": "Health",
}

# (method, path) -> (operationId, description). operationIds are stable names for SDKs.
_OPERATIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("get", "/health"): ("getHealth", "Returns 200 when the database and Redis respond, else 503."),
    ("post", "/api/v1/auth/register"): (
        "register",
        "Create a tenant with a dashboard password. Returns the tenant and its first API key, "
        "which is shown only once.",
    ),
    ("post", "/api/v1/auth/login"): (
        "login",
        "Exchange email and password for a 7-day session key, used by the dashboard. "
        "Limited to 10 attempts per minute per email.",
    ),
    ("post", "/api/v1/auth/logout"): (
        "logout",
        "Revoke the session key used for this request. Integration keys are not affected.",
    ),
    ("get", "/api/v1/me"): ("getMe", "The tenant that owns the API key."),
    ("put", "/api/v1/me/domain-config"): (
        "updateDomainConfig",
        "Replace the domain config. `rebuild_recommended` is true when embedded fields or "
        "filter fields changed; call `POST /index/rebuild` to apply them to existing items.",
    ),
    ("post", "/api/v1/me/delete"): (
        "deleteAccount",
        "Permanently delete the tenant with its items, keys, logs and vector index. Requires "
        "the account email, and the password if the account has one.",
    ),
    ("get", "/api/v1/me/api-keys"): (
        "listApiKeys",
        "API keys of the tenant, newest first. Only the prefix is returned, never the key.",
    ),
    ("post", "/api/v1/me/api-keys"): (
        "createApiKey",
        "Create an API key. The plain key is in the response once; store it securely.",
    ),
    ("delete", "/api/v1/me/api-keys/{key_id}"): (
        "revokeApiKey",
        "Revoke a key. Requests with it get 401 immediately.",
    ),
    ("post", "/api/v1/items/upload"): (
        "uploadItems",
        "Upload up to 1000 items. Each needs an `external_id`; any other fields are stored, "
        "and the domain config decides which are embedded and which become filters. "
        "Re-uploading an `external_id` updates the item.\n\n"
        "With `async: true` (default) the response is `202` with a `batch_id` to poll. With "
        "`async: false`, up to 50 items are embedded before responding.",
    ),
    ("post", "/api/v1/items/upload-csv"): (
        "uploadItemsCsv",
        "Upload a UTF-8 CSV (up to 10,000 rows, 10 MB). Headers are matched to your fields "
        "ignoring case and punctuation; the id column may be `external_id`, `id`, `item_id` "
        "or `sku`. Always asynchronous.",
    ),
    ("get", "/api/v1/items/batch/{batch_id}"): (
        "getBatchStatus",
        "Progress of an upload or rebuild. Poll until `status` is `DONE` or `PARTIAL_FAIL`.",
    ),
    ("get", "/api/v1/items"): (
        "listItems",
        "Items, newest first, 20 per page. Filter by `status` or by part of the `external_id`.",
    ),
    ("delete", "/api/v1/items"): (
        "deleteAllItems",
        "Delete every item of the tenant from the database and the vector index.",
    ),
    ("post", "/api/v1/items/bulk-delete"): (
        "bulkDeleteItems",
        "Delete up to 1000 items by `external_id`. Unknown ids are reported, not an error.",
    ),
    ("get", "/api/v1/items/{external_id}"): (
        "getItem",
        "One item with its uploaded data, embedding status and filter metadata.",
    ),
    ("delete", "/api/v1/items/{external_id}"): (
        "deleteItem",
        "Delete an item from the vector index, then from the database.",
    ),
    ("get", "/api/v1/index/stats"): (
        "getIndexStats",
        "Vector count and dimension of the tenant's index, with item counts by status.",
    ),
    ("post", "/api/v1/index/rebuild"): (
        "rebuildIndex",
        "Re-embed every item, e.g. after changing the domain config. Returns a batch to poll.",
    ),
    ("post", "/api/v1/recommend/by-text"): (
        "recommendByText",
        "Items most similar to free text. Results are cached for 5 minutes (`X-Cache` header) "
        "unless `include_raw_data` is true.",
    ),
    ("post", "/api/v1/recommend/by-item"): (
        "recommendByItem",
        'Items most similar to one of your items ("similar jobs", "more like this"). '
        "The item itself is never returned. `409` if it is not embedded yet.",
    ),
    ("post", "/api/v1/recommend/by-profile"): (
        "recommendByProfile",
        "Items matching a profile, e.g. a candidate's skills and experience. Every profile "
        "field is used, whatever its name; fields matching your searchable fields come first.",
    ),
    ("post", "/api/v1/recommend/batch"): (
        "recommendBatch",
        "Up to 20 text queries in one request, embedded together and searched concurrently.",
    ),
    ("post", "/api/v1/recommend/feedback"): (
        "submitFeedback",
        "Record how a user reacted to a recommended item, using the `query_id` of the "
        "recommendation. Collected to improve ranking.",
    ),
    ("get", "/api/v1/analytics/overview"): (
        "getAnalyticsOverview",
        "Item count, query volume today and this month, average latency and top items.",
    ),
    ("get", "/api/v1/analytics/feedback-summary"): (
        "getFeedbackSummary",
        "Feedback counts by type for the last `days` days.",
    ),
    ("get", "/api/v1/analytics/usage"): (
        "getUsage",
        "Daily volume and latency, query types and cache hit rate for the last `days` days.",
    ),
    ("post", "/api/v1/tenants"): (
        "createTenant",
        "Create a tenant without a password. New integrations should use `/auth/register`.",
    ),
    ("get", "/api/v1/tenants/{tenant_id}"): ("getTenant", "A tenant by id."),
    ("post", "/api/v1/tenants/{tenant_id}/api-keys"): (
        "createTenantApiKey",
        "Create an API key for a tenant by id.",
    ),
    ("get", "/api/v1/tenants/{tenant_id}/api-keys"): (
        "listTenantApiKeys",
        "API keys of a tenant by id.",
    ),
    ("delete", "/api/v1/tenants/{tenant_id}/api-keys/{key_id}"): (
        "revokeTenantApiKey",
        "Revoke an API key of a tenant by id.",
    ),
}

HR_ITEMS = [
    {
        "external_id": "job-101",
        "title": "Senior Python Developer",
        "description": "Build FastAPI microservices for a hiring platform",
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "location": "Bangalore",
        "department": "Engineering",
        "employment_type": "full_time",
    },
    {
        "external_id": "job-102",
        "title": "Data Scientist",
        "description": "Train ranking models on user behaviour data",
        "skills": ["Python", "PyTorch", "SQL"],
        "location": "Remote",
        "department": "Data",
        "employment_type": "full_time",
    },
]
FOOD_ITEMS = [
    {
        "external_id": "dish-1",
        "name": "Penne Arrabbiata",
        "description": "Penne in a fiery tomato, garlic and red chilli sauce",
        "cuisine": "Italian",
        "ingredients": ["penne", "tomato", "chilli"],
        "dietary_tags": ["vegetarian"],
        "price_range": "$$",
    },
    {
        "external_id": "dish-2",
        "name": "Paneer Tikka",
        "description": "Smoky grilled cottage cheese marinated in spiced yogurt",
        "cuisine": "North Indian",
        "ingredients": ["paneer", "yogurt"],
        "dietary_tags": ["vegetarian", "gluten_free"],
        "price_range": "$$",
    },
]
_RESULTS_EXAMPLE = {
    "results": [
        {
            "rank": 1,
            "external_id": "job-101",
            "score": 0.7677,
            "score_label": "Good Match",
            "metadata": {"location": "Bangalore", "department": "Engineering"},
        },
        {
            "rank": 2,
            "external_id": "job-106",
            "score": 0.6809,
            "score_label": "Fair Match",
            "metadata": {"location": "Remote", "department": "Engineering"},
        },
    ],
    "total": 2,
    "query_id": "54c4f208-3af7-4790-acdb-1b246e04a27f",
    "latency_ms": 312,
    "request_id": "74996b11-d452-4da6-8046-b83104c0e312",
}

# (method, path) -> request body examples.
_REQUEST_EXAMPLES: dict[tuple[str, str], dict[str, dict[str, Any]]] = {
    ("post", "/api/v1/items/upload"): {
        "hr": {
            "summary": "HR: job postings",
            "value": {"async": True, "items": HR_ITEMS},
        },
        "food": {
            "summary": "Food: dishes, embedded before responding",
            "value": {"async": False, "items": FOOD_ITEMS},
        },
    },
    ("post", "/api/v1/recommend/by-text"): {
        "hr": {
            "summary": "HR: jobs for a search phrase, in two cities",
            "value": {
                "query": "senior python developer with fastapi experience",
                "top_k": 10,
                "filters": {"location": ["Bangalore", "Remote"]},
            },
        },
        "food": {
            "summary": "Food: vegetarian dishes under a price",
            "value": {
                "query": "spicy vegetarian pasta",
                "top_k": 5,
                "filters": {"dietary_tags": "vegetarian", "price_range": ["$", "$$"]},
            },
        },
    },
    ("post", "/api/v1/recommend/by-profile"): {
        "candidate": {
            "summary": "HR: jobs matching a candidate",
            "value": {
                "profile": {
                    "skills": "Python, FastAPI, PostgreSQL",
                    "experience": "5 years backend development",
                    "preferred_location": "Remote",
                },
                "top_k": 10,
                "filters": {"department": "Engineering"},
            },
        },
    },
}

# (method, path, status) -> response example.
_RESPONSE_EXAMPLES: dict[tuple[str, str, str], dict[str, Any]] = {
    ("post", "/api/v1/recommend/by-text", "200"): _RESULTS_EXAMPLE,
    ("post", "/api/v1/recommend/by-profile", "200"): _RESULTS_EXAMPLE,
    ("post", "/api/v1/items/upload", "202"): {
        "batch_id": "48de4e95-c0e1-4d23-b77c-a12ebfe51b42",
        "status": "PENDING",
        "total_items": 2,
        "processed_items": 0,
        "failed_items": 0,
        "progress_percentage": 0.0,
        "created_at": "2026-09-24T04:16:16Z",
        "completed_at": None,
        "mode": "async",
        "status_url": "/api/v1/items/batch/48de4e95-c0e1-4d23-b77c-a12ebfe51b42",
    },
}


def build_openapi(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title="RecoEngine API",
        version=app.version,
        description=API_DESCRIPTION,
        routes=app.routes,
        tags=TAGS,
        servers=[
            {"url": "https://api.recoengine.io", "description": "Production"},
            {"url": "http://localhost:8000", "description": "Local development"},
        ],
    )
    schema["info"]["contact"] = {"name": "RecoEngine", "url": "https://recoengine.io"}
    schema["info"]["license"] = {"name": "MIT", "identifier": "MIT"}

    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            operation["tags"] = [_TAG_NAMES.get(t, t) for t in operation.get("tags", [])]
            if doc := _OPERATIONS.get((method, path)):
                operation["operationId"], description = doc
                operation.setdefault("description", description)
            if examples := _REQUEST_EXAMPLES.get((method, path)):
                content = operation.get("requestBody", {}).get("content", {})
                if "application/json" in content:
                    content["application/json"]["examples"] = deepcopy(examples)
            for status, response in operation.get("responses", {}).items():
                example = _RESPONSE_EXAMPLES.get((method, path, status))
                media = response.get("content", {}).get("application/json")
                if example is not None and media is not None:
                    media["example"] = deepcopy(example)
    return schema


def install_openapi(app: FastAPI) -> None:
    """Serve the customised schema from app.openapi() (and so from /openapi.json)."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
