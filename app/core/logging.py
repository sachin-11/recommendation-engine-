"""Structured logging with structlog.

Log lines carry any context bound with `structlog.contextvars` (the request middleware
binds `request_id`). Production emits JSON lines; other environments emit key=value text.
"""

import logging

import structlog

from app.core.config import settings


def configure_sentry(component: str) -> None:
    """Report unhandled errors to Sentry when SENTRY_DSN is set. No personal data is sent."""
    if settings.SENTRY_DSN is None:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN.get_secret_value(),
        environment=settings.APP_ENV,
        release=f"recoengine@{settings.APP_VERSION}",
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )
    sentry_sdk.set_tag("component", component)


def configure_structlog() -> None:
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.processors.KeyValueRenderer(key_order=["timestamp", "level", "event"])
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.LOG_LEVEL]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
