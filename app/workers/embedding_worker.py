"""Standalone embedding worker: polls the database for PENDING items and embeds them.

Run with:  python -m app.workers.embedding_worker

It complements the API's background tasks. It picks up items left PENDING when OpenAI
or Pinecone was down, items whose API process died mid-batch, and large backlogs. Any
number of workers can run at once; items are claimed atomically.
"""

import asyncio
import contextlib
import logging
import signal

from prometheus_client import start_http_server

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.logging import configure_sentry
from app.core.redis_client import create_redis_client
from app.core.tracing import configure_tracing
from app.services.embedding.openai_embedder import OpenAIEmbedder, get_openai_client
from app.services.embedding.pinecone_service import get_pinecone_service
from app.services.embedding.pipeline import EmbeddingPipeline, UpstreamUnavailableError

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("embedding_worker")

# How long to wait before retrying after OpenAI/Pinecone was unavailable.
UPSTREAM_BACKOFF_SECONDS = 30.0
ERROR_BACKOFF_SECONDS = 10.0


async def run(stop: asyncio.Event) -> None:
    configure_sentry("worker")
    configure_tracing()
    if settings.WORKER_METRICS_PORT:
        start_http_server(settings.WORKER_METRICS_PORT)
    redis = await create_redis_client()
    pipeline = EmbeddingPipeline(
        AsyncSessionLocal, OpenAIEmbedder(get_openai_client(), redis), get_pinecone_service()
    )
    logger.info(
        "Embedding worker started (poll every %.1fs)", settings.WORKER_POLL_INTERVAL_SECONDS
    )
    try:
        while not stop.is_set():
            delay = settings.WORKER_POLL_INTERVAL_SECONDS
            try:
                claimed = await pipeline.process_pending()
                if claimed:
                    logger.info("Processed %d items", claimed)
                    delay = 0  # there may be more work; go again straight away
            except UpstreamUnavailableError as exc:
                logger.warning(
                    "Upstream unavailable, retrying in %.0fs: %s", UPSTREAM_BACKOFF_SECONDS, exc
                )
                delay = UPSTREAM_BACKOFF_SECONDS
            except Exception:
                logger.exception("Worker iteration failed")
                delay = ERROR_BACKOFF_SECONDS
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
    finally:
        await redis.aclose()
        await engine.dispose()
        logger.info("Embedding worker stopped")


def main() -> None:
    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Windows event loops lack signal handlers; Ctrl+C still raises KeyboardInterrupt.
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        await run(stop)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())


if __name__ == "__main__":
    main()
