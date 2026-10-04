"""Shared httpx classification + wiring so every HTTP-backed provider gets the same
resilience behaviour (Master Prompt §78) without repeating it: transient vs permanent
errors, `Retry-After` parsing, and a small wrapper around call_resilient.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, TypeVar

import httpx

from app.core.config import settings
from app.core.provider_health import record as record_health
from app.core.redaction import redact_secrets
from app.core.resilience import RetryPolicy, call_resilient

T = TypeVar("T")

# 429 and 5xx are worth retrying (rate limit / transient server trouble); everything else in
# 4xx means OUR request was wrong and repeating it changes nothing.
TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def is_transient_http_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    # Timeouts, connection errors, DNS failures: the network/provider, not our request.
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError))


def http_retry_after(exc: BaseException) -> Optional[float]:
    if isinstance(exc, httpx.HTTPStatusError):
        header = exc.response.headers.get("Retry-After")
        if header and header.strip().isdigit():
            return float(header)
    return None


def default_retry_policy() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.PROVIDER_RETRY_ATTEMPTS,
        base_delay=settings.PROVIDER_RETRY_BASE_DELAY_SECONDS,
        max_delay=settings.PROVIDER_RETRY_MAX_DELAY_SECONDS,
    )


async def resilient_request(
    provider: str,
    operation: Callable[[], Awaitable[T]],
    *,
    retry: Optional[RetryPolicy] = None,
    idempotent: bool = True,
) -> T:
    """`idempotent=False` for calls that must never be silently repeated (e.g. a payment
    charge) — one attempt, still behind the circuit breaker."""
    return await call_resilient(
        provider, operation, is_transient=is_transient_http_error,
        retry=retry or (default_retry_policy() if idempotent else RetryPolicy(max_attempts=1)),
        retry_after=http_retry_after, observer=record_health,
    )


def safe_error_text(exc: httpx.HTTPStatusError, *, limit: int = 300) -> str:
    return redact_secrets(exc.response.text, max_length=limit)
