"""
Provider resilience (Master Prompt §78): timeouts, retries with exponential backoff and
jitter, and circuit breakers.

Why: an external provider WILL fail. Without protection, a provider that is down makes
every request wait out its full timeout (and a retry storm makes recovery harder). With it:

  * transient failures (timeouts, connection errors, 5xx, 429) are retried a bounded
    number of times with exponential backoff + jitter, honouring a provider's
    `Retry-After` (capped) — but ONLY for operations that are safe to repeat;
  * permanent failures (4xx: bad request, bad credentials) are NOT retried and do NOT
    count against the provider's health — the provider answered, our request was wrong;
  * after `failure_threshold` consecutive transient failures the breaker OPENS: calls
    fail immediately (no 15-second waits, no hammering) for `recovery_seconds`; then ONE
    probe is let through (half-open) — success closes it, failure re-opens it.

State is per process (each API/worker process learns independently, which is exactly
what protects that process's event loop); aggregate health is published to Redis by
app/core/provider_health.py. Pure stdlib apart from the exception base class, and every
time source is injectable, so the whole state machine is unit-tested deterministically.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

from app.core.exceptions import ProviderUnavailableError

T = TypeVar("T")

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


class CircuitOpenError(ProviderUnavailableError):
    """Raised without contacting the provider because its circuit is open."""

    def __init__(self, provider: str, retry_in_seconds: float):
        super().__init__(
            "This service is temporarily unavailable. Please try again shortly.",
            details={"provider": provider, "retry_in_seconds": max(0, round(retry_in_seconds))},
        )


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.4
    max_delay: float = 5.0
    jitter: float = 0.25          # +/- fraction applied to each delay

    def delay(self, attempt: int, *, retry_after: Optional[float] = None, rand: Callable[[], float] = random.random) -> float:
        """Seconds to wait after failed attempt number `attempt` (1-based)."""
        delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        delay *= 1 + self.jitter * (2 * rand() - 1)
        if retry_after is not None:
            delay = max(delay, min(retry_after, self.max_delay))     # honour the provider, but never wait forever
        return max(0.0, delay)


NO_RETRY = RetryPolicy(max_attempts=1)


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self.state = CLOSED
        self.consecutive_failures = 0
        self.opened_at: Optional[float] = None
        self._probe_in_flight = False

    def allow(self) -> bool:
        """May a call proceed right now? (Also performs the open -> half-open transition.)"""
        if self.state == CLOSED:
            return True
        if self.state == OPEN:
            if self._clock() - (self.opened_at or 0.0) >= self.recovery_seconds:
                self.state, self._probe_in_flight = HALF_OPEN, True
                return True
            return False
        # HALF_OPEN: exactly one probe at a time; everyone else fails fast.
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def retry_in(self) -> float:
        if self.state != OPEN or self.opened_at is None:
            return 0.0
        return max(0.0, self.recovery_seconds - (self._clock() - self.opened_at))

    def record_success(self) -> None:
        self.state, self.consecutive_failures, self.opened_at, self._probe_in_flight = CLOSED, 0, None, False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self._probe_in_flight = False
        if self.state == HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            self.state, self.opened_at = OPEN, self._clock()

    def record_neutral(self) -> None:
        """The provider answered (e.g. 4xx): it is up, so this is not a failure — but a half-open
        probe slot must be released."""
        self._probe_in_flight = False
        if self.state == HALF_OPEN:
            self.record_success()

    @property
    def is_open(self) -> bool:
        return self.state == OPEN


# ---------------------------------------------------------------------------
# Registry (one breaker per provider name, per process)
# ---------------------------------------------------------------------------
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(name: str) -> CircuitBreaker:
    breaker = _breakers.get(name)
    if breaker is None:
        from app.core.config import settings

        breaker = CircuitBreaker(
            name, failure_threshold=settings.CIRCUIT_FAILURE_THRESHOLD, recovery_seconds=settings.CIRCUIT_RECOVERY_SECONDS
        )
        _breakers[name] = breaker
    return breaker


def all_breakers() -> dict[str, CircuitBreaker]:
    return dict(_breakers)


def reset_breakers() -> None:
    _breakers.clear()


# ---------------------------------------------------------------------------
# The call wrapper
# ---------------------------------------------------------------------------
Observer = Callable[[str, bool, float, Optional[BaseException], str], Awaitable[None]]


async def call_resilient(
    name: str,
    operation: Callable[[], Awaitable[T]],
    *,
    is_transient: Callable[[BaseException], bool],
    retry: RetryPolicy = RetryPolicy(),
    retry_after: Callable[[BaseException], Optional[float]] = lambda exc: None,
    breaker: Optional[CircuitBreaker] = None,
    observer: Optional[Observer] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Run `operation` under the provider's circuit breaker with bounded retries.

    Raises CircuitOpenError immediately if the circuit is open; otherwise re-raises the
    operation's own exception once retries are exhausted (or at once if it is permanent)."""
    breaker = breaker or get_breaker(name)

    for attempt in range(1, retry.max_attempts + 1):
        if not breaker.allow():
            raise CircuitOpenError(name, breaker.retry_in())

        started = clock()
        try:
            result = await operation()
        except Exception as exc:  # noqa: BLE001
            elapsed = clock() - started
            if not is_transient(exc):
                breaker.record_neutral()
                await _observe(observer, name, True, elapsed, exc, breaker)      # the provider answered
                raise
            breaker.record_failure()
            await _observe(observer, name, False, elapsed, exc, breaker)
            if attempt >= retry.max_attempts or breaker.is_open:
                raise
            await sleep(retry.delay(attempt, retry_after=retry_after(exc)))
            continue

        breaker.record_success()
        await _observe(observer, name, True, clock() - started, None, breaker)
        return result

    raise RuntimeError("unreachable")  # pragma: no cover


async def _observe(observer: Optional[Observer], name: str, ok: bool, elapsed: float, exc, breaker: CircuitBreaker) -> None:
    if observer is None:
        return
    try:
        await observer(name, ok, elapsed, exc, breaker.state)
    except Exception:  # noqa: BLE001 - observability must never break the call it observes
        pass
