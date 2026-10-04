"""Tour-Wayva Backend — FastAPI application entrypoint."""
from __future__ import annotations

import random
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routers import api_v1_router
from app.core.config import settings
from app.core.error_utils import http_error_code, sanitize_validation_errors
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start-up: bring the RBAC catalog in the database in step with the code
    (idempotent, safe under concurrent replicas). A failure is logged, not fatal:
    the migrations already seeded the catalog, so the API can still serve."""
    try:
        from app.db.session import AsyncSessionLocal
        from app.modules.admin.rbac_sync import sync_rbac_catalog

        async with AsyncSessionLocal() as db:
            await sync_rbac_catalog(db)
    except Exception as exc:  # noqa: BLE001
        logger.error("rbac_sync_failed", error=str(exc))
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Tour-Wayva API",
    description="Production backend for the Tour-Wayva AI travel intelligence platform.",
    version="2.0.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

# Explicit origins only. staging/production refuse to start without them
# (see Settings._validate_environment_guards); in development/testing an
# empty list falls back to the usual local frontend origins — never "*"
# combined with credentials.
_DEV_CORS_ORIGINS = ["http://localhost:3000", "http://localhost:5173", "https://tourwayva.vercel.app"]
_cors_origins = settings.BACKEND_CORS_ORIGINS or _DEV_CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    """Reject oversized bodies up front from Content-Length, before any
    multipart parsing spools them. (Defined BEFORE add_request_id so it is
    the inner middleware and request.state.request_id already exists.)"""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.MAX_REQUEST_BODY_MB * 1024 * 1024:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={
                "error_code": "payload_too_large",
                "message": f"Request body exceeds the {settings.MAX_REQUEST_BODY_MB}MB limit.",
                "request_id": getattr(request.state, "request_id", None),
                "details": {},
            },
        )
    return await call_next(request)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def record_api_usage(request: Request, call_next):
    """Best-effort inbound request logging (Master Prompt §5 `api_usage`) — a durable,
    queryable record of traffic shape distinct from the security/audit logs (which track
    WHO did WHAT) and from ai_usage_records/provider_usage (external calls). Sampled via
    API_USAGE_SAMPLE_RATE; failures here never affect the response."""
    started = time.monotonic()
    response = await call_next(request)
    if settings.API_USAGE_SAMPLE_RATE >= 1.0 or random.random() < settings.API_USAGE_SAMPLE_RATE:
        try:
            route = request.scope.get("route")
            path = route.path if route is not None else request.url.path   # template, not raw path (bounded cardinality)
            from app.db.session import AsyncSessionLocal
            from app.repositories.provider_usage_repository import ProviderUsageRepository

            user_id = getattr(getattr(request.state, "user", None), "id", None)
            async with AsyncSessionLocal() as db:
                await ProviderUsageRepository(db).record_api_call(
                    user_id=user_id, method=request.method, path=path, status_code=response.status_code,
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_usage_record_failed", error=str(exc))
    return response


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Baseline secure headers (Master Blueprint §64). HSTS is only
    sent when the app believes it's actually being served over HTTPS
    (production) — sending it over plain HTTP in local development
    would be actively misleading."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "app_error",
        error_code=exc.error_code,
        message=exc.message,
        path=request.url.path,
        request_id=request_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "request_id": request_id,
            "details": exc.details,
        },
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_handler(request: Request, exc: ValidationError):
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": "validation_error",
            "message": "Invalid input.",
            "request_id": request_id,
            "details": {"errors": sanitize_validation_errors(exc.errors())},
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """FastAPI's own request-body/query validation errors, in the same
    envelope as every other error — and WITHOUT echoing submitted values
    (which include passwords on auth endpoints)."""
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": "validation_error",
            "message": "Invalid input.",
            "request_id": request_id,
            "details": {"errors": sanitize_validation_errors(exc.errors())},
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Routing-level errors (404 unknown path, 405 wrong method, ...)."""
    request_id = getattr(request.state, "request_id", None)
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": http_error_code(exc.status_code),
            "message": message,
            "request_id": request_id,
            "details": {},
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.error("unhandled_exception", error=str(exc), path=request.url.path, request_id=request_id)
    # Never leak stack traces, SQL errors, or internal details to the client.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_code": "internal_server_error",
            "message": "Something went wrong. Please try again.",
            "request_id": request_id,
            "details": {},
        },
    )


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}


@app.get("/health/live", tags=["Health"])
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready", tags=["Health"])
async def health_ready():
    """Verifies critical dependencies (DB, Redis) are reachable.
    Never exposes connection strings or infra details in the response."""
    checks = {"database": "unknown", "redis": "unknown"}

    try:
        from sqlalchemy import text

        from app.db.session import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("health_check_db_failed", error=str(exc))
        checks["database"] = "unavailable"

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("health_check_redis_failed", error=str(exc))
        checks["redis"] = "unavailable"

    overall_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if overall_ok else "not_ready", "checks": checks},
    )


app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
