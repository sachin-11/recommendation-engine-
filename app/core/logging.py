"""Structured logging with structlog.

Log lines carry any context bound with `structlog.contextvars` (the request middleware
binds `request_id`). Production emits JSON lines; other environments emit key=value text.
"""

import logging

import structlog

from app.core.config import settings


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
