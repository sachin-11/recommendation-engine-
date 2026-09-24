"""Prometheus metrics.

The API serves them at /metrics; the embedding worker serves its own on
WORKER_METRICS_PORT (it is a separate process with separate counters).
"""

from prometheus_client import Counter, Gauge, Histogram

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
ITEMS = Gauge(
    "items_total",
    "Items per tenant and embedding status (refreshed on each scrape).",
    ["tenant_id", "status"],
)
EMBEDDING_PIPELINE_DURATION = Histogram(
    "embedding_pipeline_duration_seconds",
    "Time to embed and store one chunk of items.",
    ["outcome"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)
