# syntax=docker/dockerfile:1
# Stages:
#   builder  – installs dependencies into /opt/venv with Poetry
#   dev      – runtime + dev dependencies + Poetry (used by docker-compose.yml)
#   runtime  – production image (the default): no dev deps, no Poetry, non-root
ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- builder
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache

RUN python -m venv /opt/poetry \
    && /opt/poetry/bin/pip install "poetry>=2.0,<3.0" \
    && python -m venv /opt/venv

WORKDIR /build
COPY pyproject.toml poetry.lock ./
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then /opt/poetry/bin/poetry install --no-root --with dev; \
    else /opt/poetry/bin/poetry install --no-root --only main; fi

# Bake the tokenizer used for embedding truncation, so no request has to download it.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# ---------------------------------------------------------------- shared runtime base
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/tiktoken_cache /opt/tiktoken_cache
WORKDIR /app
EXPOSE 8000

# ---------------------------------------------------------------- dev
FROM base AS dev
COPY --from=builder /opt/poetry /opt/poetry
RUN ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry
COPY --chown=appuser:appuser . .
USER appuser
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir app"]

# ---------------------------------------------------------------- runtime (production)
FROM base AS runtime
COPY --chown=appuser:appuser . .
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1
# Migrations are run by the deploy (scripts/deploy.sh, or Railway's pre-deploy command), once,
# not by every replica. PORT is set by platforms such as Railway; 8000 otherwise.
# PROMETHEUS_MULTIPROC_DIR lets /metrics add up the counters of every uvicorn process; it is
# emptied on start so counters of a previous run do not linger. Only the API sets it: the
# worker runs another command from this image and keeps plain in-process metrics.
CMD ["sh", "-c", "export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-metrics && rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --workers ${WEB_CONCURRENCY:-2}"]
