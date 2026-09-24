"""Optional LangSmith tracing of the AI path: embeddings, vector search, recommendations.

Enabled with LANGSMITH_TRACING=true (or LANGCHAIN_TRACING_V2=true) and an API key.
Traces contain query text and the text built from items; set LANGSMITH_HIDE_INPUTS=true to
keep inputs out of LangSmith. Vectors are never sent, only their count and dimension.
When tracing is off, the decorators below cost one environment check per call.
"""

import logging
import os
from collections.abc import Callable
from typing import Any, Literal, TypeVar, cast

import structlog
from langsmith import traceable

from app.core.config import settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
RunType = Literal["chain", "embedding", "retriever"]

MAX_TRACED_TEXTS = 20
MAX_TRACED_CHARS = 500


def configure_tracing() -> bool:
    """Export the settings to the environment variables the LangSmith SDK reads.
    Returns whether tracing is on."""
    enabled = bool(settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY)
    # Set both spellings: the SDK honours either, and a stale one must not re-enable it.
    for flag in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ[flag] = "true" if enabled else "false"
    if enabled:
        assert settings.LANGSMITH_API_KEY is not None
        os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY.get_secret_value()
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        if settings.LANGSMITH_ENDPOINT:
            os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
        if settings.LANGSMITH_HIDE_INPUTS:
            os.environ["LANGSMITH_HIDE_INPUTS"] = "true"
        logger.info("LangSmith tracing enabled (project %s)", settings.LANGSMITH_PROJECT)
    elif settings.LANGSMITH_TRACING:
        logger.warning("LANGSMITH_TRACING is set but LANGSMITH_API_KEY is missing; not tracing")
    return enabled


def _request_context() -> dict[str, Any]:
    context = structlog.contextvars.get_contextvars()
    return {"request_id": context["request_id"]} if "request_id" in context else {}


def clip(text: Any) -> Any:
    if isinstance(text, str) and len(text) > MAX_TRACED_CHARS:
        return text[:MAX_TRACED_CHARS] + "…"
    return text


def traced(
    name: str,
    run_type: RunType = "chain",
    *,
    inputs: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    outputs: Callable[[Any], dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    """`langsmith.traceable` with summarised inputs/outputs and the request id attached.

    `inputs` receives the call's bound arguments (without `self`); `outputs` the return value.
    """

    def process_inputs(raw: dict[str, Any]) -> dict[str, Any]:
        raw = {k: v for k, v in raw.items() if k != "self"}
        return {**(inputs(raw) if inputs else raw), **_request_context()}

    def process_outputs(raw: Any) -> dict[str, Any]:
        if outputs is None:
            return raw if isinstance(raw, dict) else {"output": raw}
        return outputs(raw)

    def decorator(func: F) -> F:
        wrapped = traceable(
            name=name,
            run_type=run_type,
            process_inputs=process_inputs,
            process_outputs=process_outputs,
        )(func)
        # traceable keeps the call signature; it only adds an optional langsmith_extra kwarg.
        return cast(F, wrapped)

    return decorator


def set_run_usage(tokens: int, *, model: str) -> None:
    """Report token usage on the current run so LangSmith shows tokens and cost."""
    from langsmith.run_helpers import get_current_run_tree

    run = get_current_run_tree()
    if run is not None:
        run.metadata.update({"ls_provider": "openai", "ls_model_name": model})
        run.set(usage_metadata={"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens})


def add_run_metadata(**metadata: Any) -> None:
    """Attach metadata to the current run, if one is being traced."""
    from langsmith.run_helpers import get_current_run_tree

    run = get_current_run_tree()
    if run is not None:
        run.metadata.update(metadata)
