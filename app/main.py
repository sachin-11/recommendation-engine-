"""FastAPI application factory, lifespan, middleware and global error handling."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.metrics import metrics_router
from app.api.openapi import install_openapi
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import check_database, engine, get_db, ping_database
from app.core.exceptions import AppException
from app.core.logging import configure_sentry, configure_structlog
from app.core.redis_client import check_redis, create_redis_client, get_redis
from app.middleware.request_id import RequestIDMiddleware
from app.schemas.common import ErrorBody, ErrorDetail, ErrorResponse, HealthResponse
from app.services.embedding.pinecone_service import get_pinecone_service

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)
configure_structlog()
configure_sentry("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s v%s (%s)", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV)
    # Fail fast: a pod that cannot reach its dependencies should not report as started.
    await ping_database()
    logger.info("Database connection verified")
    app.state.redis = await create_redis_client()
    logger.info("Redis connection verified")
    # In the background: the first recommendation would otherwise pay for opening the
    # Pinecone index and its connection, which can exceed the 5 s query timeout.
    warm_up = asyncio.create_task(get_pinecone_service().warm_up())
    try:
        yield
    finally:
        warm_up.cancel()
        await app.state.redis.aclose()
        await engine.dispose()
        logger.info("Shutdown complete")


# --- Error handling ---


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


_HTTP_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def _app_exception(_: Request, exc: AppException) -> JSONResponse:
        details = [ErrorDetail(**d) for d in exc.details] if exc.details else None
        return _error_response(
            exc.status_code, exc.error_code, exc.message, details, headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
        message = "Resource not found" if exc.status_code == 404 else str(exc.detail)
        headers = dict(exc.headers) if exc.headers else None
        return _error_response(exc.status_code, code, message, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(part) for part in err.get("loc", ())) or None,
                message=err.get("msg", "Invalid value"),
                type=err.get("type"),
            )
            for err in jsonable_encoder(exc.errors())
        ]
        # Literal 422: Starlette renamed the constant (…_ENTITY -> …_CONTENT) across versions.
        return _error_response(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "An unexpected error occurred"
        )


# --- Health ---

health_router = APIRouter(tags=["health"])


@health_router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "A dependency is unavailable"}},
)
async def health(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> JSONResponse:
    db_ok = await check_database(db)
    redis_ok = await check_redis(redis)
    healthy = db_ok and redis_ok
    body = HealthResponse(
        status="ok" if healthy else "degraded",
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        database="ok" if db_ok else "unavailable",
        redis="ok" if redis_ok else "unavailable",
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body.model_dump(),
    )


# --- Factory ---


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    if settings.ALLOWED_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.ALLOWED_ORIGINS,
            allow_credentials=False,  # API-key auth via headers; no cookies
            allow_methods=["*"],
            allow_headers=["*"],
            # Let browser clients (the dashboard) read these response headers.
            expose_headers=["X-Cache", "X-Request-ID", "Retry-After"],
        )

    # Added last, so it wraps CORS and every route: all responses carry X-Request-ID.
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(metrics_router)
    install_openapi(app)
    return app


app = create_app()
