"""Retry policy, circuit breaker and the resilient-call wrapper — fully deterministic (fake clock/sleep)."""
from __future__ import annotations

import asyncio

import pytest

from app.core.exceptions import ProviderUnavailableError
from app.core.redaction import redact_secrets
from app.core.resilience import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    CircuitBreaker,
    CircuitOpenError,
    RetryPolicy,
    call_resilient,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Transient(Exception):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after


class Permanent(Exception):
    pass


def is_transient(exc):
    return isinstance(exc, Transient)


def retry_after(exc):
    return getattr(exc, "retry_after", None)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
def test_backoff_is_exponential_capped_and_jittered_within_bounds():
    policy = RetryPolicy(max_attempts=6, base_delay=1.0, max_delay=5.0, jitter=0.25)
    no_jitter = lambda: 0.5                                     # rand()=0.5 => factor exactly 1.0
    assert [policy.delay(a, rand=no_jitter) for a in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 5.0, 5.0]
    assert policy.delay(2, rand=lambda: 0.0) == pytest.approx(1.5) and policy.delay(2, rand=lambda: 1.0) == pytest.approx(2.5)


def test_a_providers_retry_after_is_honoured_but_capped():
    policy = RetryPolicy(base_delay=0.4, max_delay=5.0, jitter=0.0)
    assert policy.delay(1, retry_after=3.0) == 3.0
    assert policy.delay(1, retry_after=600.0) == 5.0            # never wait forever on a hostile/buggy header
    assert policy.delay(1, retry_after=0.1) == 0.4              # never LESS than our own backoff


# ---------------------------------------------------------------------------
# Circuit breaker state machine
# ---------------------------------------------------------------------------
def test_the_circuit_opens_after_consecutive_failures_and_fails_fast():
    clock = Clock()
    breaker = CircuitBreaker("p", failure_threshold=3, recovery_seconds=30, clock=clock)
    for _ in range(2):
        breaker.record_failure()
    assert breaker.state == CLOSED and breaker.allow()
    breaker.record_failure()
    assert breaker.state == OPEN and not breaker.allow()
    clock.advance(10)
    assert not breaker.allow() and breaker.retry_in() == pytest.approx(20)


def test_a_success_resets_the_failure_streak():
    breaker = CircuitBreaker("p", failure_threshold=3, clock=Clock())
    breaker.record_failure(); breaker.record_failure(); breaker.record_success(); breaker.record_failure()
    assert breaker.state == CLOSED and breaker.consecutive_failures == 1


def test_after_the_recovery_window_exactly_one_probe_is_allowed():
    clock = Clock()
    breaker = CircuitBreaker("p", failure_threshold=1, recovery_seconds=30, clock=clock)
    breaker.record_failure()
    clock.advance(30)
    assert breaker.allow() and breaker.state == HALF_OPEN       # the probe
    assert not breaker.allow()                                  # everyone else still fails fast


def test_a_successful_probe_closes_the_circuit_and_a_failed_one_reopens_it():
    clock = Clock()
    breaker = CircuitBreaker("p", failure_threshold=1, recovery_seconds=30, clock=clock)
    breaker.record_failure(); clock.advance(30); breaker.allow()
    breaker.record_success()
    assert breaker.state == CLOSED and breaker.allow()

    breaker.record_failure(); clock.advance(30); breaker.allow()
    breaker.record_failure()                                    # the probe failed
    assert breaker.state == OPEN and not breaker.allow() and breaker.retry_in() == pytest.approx(30)


def test_a_permanent_error_during_a_probe_proves_the_provider_is_up():
    clock = Clock()
    breaker = CircuitBreaker("p", failure_threshold=1, recovery_seconds=30, clock=clock)
    breaker.record_failure(); clock.advance(30); breaker.allow()
    breaker.record_neutral()
    assert breaker.state == CLOSED


# ---------------------------------------------------------------------------
# call_resilient
# ---------------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.sleeps, self.events = [], []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)

    async def observe(self, name, ok, elapsed, exc, state):
        self.events.append((name, ok, type(exc).__name__ if exc else None, state))


def _call(operation, *, breaker=None, policy=RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=5.0, jitter=0.0), rec=None):
    rec = rec or Recorder()
    breaker = breaker or CircuitBreaker("p", failure_threshold=5, clock=Clock())
    result = call_resilient("p", operation, is_transient=is_transient, retry=policy, retry_after=retry_after,
                            breaker=breaker, observer=rec.observe, sleep=rec.sleep)
    return _run(result), rec, breaker


def test_a_first_try_success_costs_nothing_extra():
    async def op():
        return "ok"

    result, rec, breaker = _call(op)
    assert result == "ok" and rec.sleeps == [] and rec.events == [("p", True, None, CLOSED)]


def test_transient_failures_are_retried_with_backoff_until_success():
    outcomes = [Transient(), Transient(), "ok"]

    async def op():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result, rec, breaker = _call(op)
    assert result == "ok" and rec.sleeps == [1.0, 2.0]                       # 1s then 2s
    assert breaker.consecutive_failures == 0                                 # the success reset the streak


def test_a_retry_after_from_the_provider_stretches_the_wait():
    outcomes = [Transient(retry_after=3.0), "ok"]

    async def op():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    _, rec, _ = _call(op)
    assert rec.sleeps == [3.0]


def test_permanent_errors_are_not_retried_and_do_not_count_against_the_provider():
    calls = []

    async def op():
        calls.append(1)
        raise Permanent("bad request")

    breaker = CircuitBreaker("p", failure_threshold=1, clock=Clock())
    with pytest.raises(Permanent):
        _call(op, breaker=breaker)
    assert calls == [1] and breaker.state == CLOSED and breaker.consecutive_failures == 0


def test_when_retries_are_exhausted_the_last_error_surfaces_and_failures_are_counted():
    async def op():
        raise Transient()

    breaker = CircuitBreaker("p", failure_threshold=10, clock=Clock())
    rec = Recorder()
    with pytest.raises(Transient):
        _call(op, breaker=breaker, rec=rec)
    assert breaker.consecutive_failures == 3 and len(rec.sleeps) == 2         # 3 attempts, 2 waits


def test_no_retry_policy_means_exactly_one_attempt():
    calls = []

    async def op():
        calls.append(1)
        raise Transient()

    with pytest.raises(Transient):
        _call(op, policy=RetryPolicy(max_attempts=1))
    assert calls == [1]


def test_the_breaker_opening_mid_retry_stops_the_retrying():
    calls = []

    async def op():
        calls.append(1)
        raise Transient()

    breaker = CircuitBreaker("p", failure_threshold=2, clock=Clock())
    with pytest.raises(Transient):
        _call(op, breaker=breaker, policy=RetryPolicy(max_attempts=5, base_delay=0.1, jitter=0.0))
    assert calls == [1, 1] and breaker.state == OPEN                          # not 5


def test_an_open_circuit_fails_immediately_without_touching_the_provider():
    calls = []

    async def op():
        calls.append(1)
        return "ok"

    clock = Clock()
    breaker = CircuitBreaker("p", failure_threshold=1, recovery_seconds=30, clock=clock)
    breaker.record_failure()
    with pytest.raises(CircuitOpenError) as caught:
        _call(op, breaker=breaker)
    assert calls == [] and isinstance(caught.value, ProviderUnavailableError)   # callers' existing handlers still apply
    assert caught.value.details == {"provider": "p", "retry_in_seconds": 30}


def test_recovery_a_probe_after_the_window_closes_the_circuit():
    clock = Clock()
    breaker = CircuitBreaker("p", failure_threshold=1, recovery_seconds=30, clock=clock)
    breaker.record_failure()
    clock.advance(31)

    async def op():
        return "back"

    result, _, breaker = _call(op, breaker=breaker)
    assert result == "back" and breaker.state == CLOSED


def test_observer_failures_never_break_the_call_they_observe():
    async def op():
        return "ok"

    async def broken_observer(*args):
        raise RuntimeError("metrics down")

    result = _run(call_resilient("p", op, is_transient=is_transient, breaker=CircuitBreaker("p", clock=Clock()),
                                 observer=broken_observer))
    assert result == "ok"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,secret", [
    ("Client error '401' for url 'https://api.weatherapi.com/v1/forecast.json?q=Paris&key=abcd1234SECRET&days=3'", "abcd1234SECRET"),
    ("GET https://api.currencyapi.com/v3/latest?apikey=cur_live_xxxxxxxx&base_currency=USD", "cur_live_xxxxxxxx"),
    ("failed: https://api.opencagedata.com/geocode/v1/json?key=oc123456&q=Lagos", "oc123456"),
    ("Authorization: Bearer abcdefghijklmnop123456", "abcdefghijklmnop123456"),
    ("provider said sk_live_abcdef1234567890", "abcdef1234567890"),
])
def test_secrets_never_survive_redaction(raw, secret):
    assert secret not in redact_secrets(raw)


def test_redaction_keeps_the_useful_parts_and_bounds_length():
    text = redact_secrets("timeout for url 'https://x.io/a?q=Paris&key=SECRET123'")
    assert "q=Paris" in text and "key=***" in text
    assert len(redact_secrets("x" * 5000)) == 300
