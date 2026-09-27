"""Prometheus metrics.

The API serves them at /metrics; the embedding worker serves its own on
WORKER_METRICS_PORT (it is a separate process with separate counters).

Production runs several uvicorn processes. With PROMETHEUS_MULTIPROC_DIR set (the runtime
image sets it), each process writes its counters and histograms there and /metrics adds them
up, so every scrape sees the whole API rather than one process. Values read from the database
(items per status, backlog age) are computed at scrape time instead; see app/api/metrics.py.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Histogram

RECO_REQUESTS = Counter(
    "reco_requests_total",
    "Recommendation queries served.",
    ["tenant_id", "query_type"],
)
RECO_LATENCY = Histogram(
    "reco_latency_seconds",
    "Recommendation latency, from request start to response.",
    ["query_type"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 2.0, 5.0, 10.0),
)
EMBEDDING_TOKENS = Counter(
    "openai_embedding_tokens_total",
    "OpenAI embedding tokens used.",
    ["source", "model"],
)
EMBEDDING_PIPELINE_DURATION = Histogram(
    "embedding_pipeline_duration_seconds",
    "Time to embed and store one chunk of items.",
    ["outcome"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# --- HTTP ---

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled by the API, by route template and status code.",
    ["method", "route", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration, by route template.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# --- Calls to OpenAI and Pinecone ---

EXTERNAL_CALLS = Counter(
    "external_calls_total",
    "Calls to external services, one per attempt (retries count separately).",
    ["service", "operation", "outcome"],
)
EXTERNAL_CALL_DURATION = Histogram(
    "external_call_duration_seconds",
    "Duration of one call to an external service.",
    ["service", "operation"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)


def call_outcome(exc: BaseException) -> str:
    """A small, fixed set of outcomes, so alert rules can tell throttling from outages.
    Duck-typed, so it covers the OpenAI and Pinecone SDKs without importing them."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(exc, TimeoutError) or "Timeout" in name:
        return "timeout"
    if status == 429 or "RateLimit" in name:
        return "rate_limited"
    if "NotFound" in name or status == 404:
        return "not_found"
    if "Connection" in name:
        return "connection_error"
    if isinstance(status, int) and 400 <= status < 500:
        return "client_error"
    return "error"


@contextmanager
def track_external_call(service: str, operation: str) -> Iterator[None]:
    """Count and time the enclosed call. Exceptions are recorded and re-raised."""
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException as exc:
        outcome = call_outcome(exc)
        raise
    finally:
        EXTERNAL_CALL_DURATION.labels(service, operation).observe(time.perf_counter() - started)
        EXTERNAL_CALLS.labels(service, operation, outcome).inc()
