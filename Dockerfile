FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Poetry lives in its own venv; app dependencies go into /opt/venv (outside /app,
# so the dev bind-mount of the source tree doesn't hide them).
RUN python -m venv /opt/poetry \
    && /opt/poetry/bin/pip install "poetry>=2.0,<3.0" \
    && ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry \
    && python -m venv /opt/venv

WORKDIR /app

COPY pyproject.toml poetry.lock ./
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then poetry install --no-root --with dev; \
    else poetry install --no-root --only main; fi

COPY . .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
