"""Currency rates: the keyless fallback is opt-in, never invents a rate, and only runs when the primary provider is unavailable."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import httpx

from app.core.config import settings
from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.resilience import reset_breakers
from app.modules.currency import service as svc
from app.providers.currency import open_er_api_provider as oer
from app.providers.currency.interface import ExchangeRate


def _run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload, self.headers, self.text = status, payload if payload is not None else {}, {}, str(payload)
        self.request = httpx.Request("GET", "https://open.er-api.com/v6/latest/USD")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=self)


def _client(monkeypatch, response):
    seen = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            seen["url"] = url
            return response

    monkeypatch.setattr(oer.httpx, "AsyncClient", FakeClient)
    reset_breakers()
    return seen


def test_open_access_rate_is_read_from_the_documented_success_shape(monkeypatch):
    seen = _client(monkeypatch, _Resp(payload={"result": "success", "base_code": "USD", "rates": {"USD": 1, "NGN": 1534.5}}))
    r = _run(oer.OpenErApiProvider().get_latest_rate("usd", "ngn"))
    assert (r.base, r.target, r.rate, r.provider) == ("USD", "NGN", 1534.5, "exchangerate-api-open")
    assert seen["url"] == "https://open.er-api.com/v6/latest/USD"


def test_an_error_body_unknown_currency_or_nonsense_rate_is_never_turned_into_a_rate(monkeypatch):
    _client(monkeypatch, _Resp(payload={"result": "error", "error-type": "unsupported-code"}))
    for payload, exc in ((_Resp(payload={"result": "error", "error-type": "unsupported-code"}), ProviderUnavailableError),
                         (_Resp(payload={"result": "success", "rates": {"EUR": 0.9}}), NotFoundError),
                         (_Resp(payload={"result": "success", "rates": {"NGN": 0}}), NotFoundError),
                         (_Resp(payload={"result": "success", "rates": {"NGN": "lots"}}), NotFoundError)):
        _client(monkeypatch, payload)
        try:
            _run(oer.OpenErApiProvider().get_latest_rate("USD", "NGN"))
        except exc:
            continue
        raise AssertionError(f"expected {exc.__name__}")


def test_a_rate_limit_is_an_outage_not_a_rate(monkeypatch):
    _client(monkeypatch, _Resp(status=429, payload={"result": "error"}))
    try:
        _run(oer.OpenErApiProvider().get_latest_rate("USD", "NGN"))
    except ProviderUnavailableError:
        return
    raise AssertionError("expected ProviderUnavailableError")


class _Down:
    async def get_latest_rate(self, base, target):
        raise ProviderUnavailableError("Currency lookup failed. Please try again.")


class _Backup:
    def __init__(self):
        self.calls = 0

    async def get_latest_rate(self, base, target):
        self.calls += 1
        return ExchangeRate(base, target, 1500.0, datetime.now(timezone.utc), "backup")


class Env:
    def __init__(self, monkeypatch, fallback):
        self.store = {}
        env = self

        class FakeCache:
            @staticmethod
            async def get_json(key):
                return env.store.get(key)

            @staticmethod
            async def set_json(key, value, ttl):
                env.store[key] = value

        async def none(*a, **k):
            return None

        monkeypatch.setattr(svc, "CacheService", FakeCache)
        monkeypatch.setattr(svc.durable_cache, "get_currency_rate", none)
        monkeypatch.setattr(svc.durable_cache, "set_currency_rate", none)
        monkeypatch.setattr(svc, "_provider", _Down())
        monkeypatch.setattr(settings, "CURRENCY_FALLBACK_PROVIDER", fallback)


def test_without_the_opt_in_a_primary_outage_is_reported_and_nothing_is_invented(monkeypatch):
    Env(monkeypatch, "")
    try:
        _run(svc.CurrencyService().get_rate("USD", "NGN"))
    except ProviderUnavailableError:
        return
    raise AssertionError("expected ProviderUnavailableError")


def test_with_the_opt_in_the_backup_answers_when_the_primary_is_down(monkeypatch):
    env = Env(monkeypatch, "open_er_api")
    backup = _Backup()
    monkeypatch.setattr(svc, "_fallback_provider", lambda: backup)
    out = _run(svc.CurrencyService().get_rate("USD", "NGN"))
    assert out.rate == 1500.0 and out.provider == "backup" and out.source == "live"
    again = _run(svc.CurrencyService().get_rate("USD", "NGN"))
    assert again.source == "cache" and backup.calls == 1 and env.store          # the backup's answer is cached like any other


def test_only_the_documented_fallback_name_enables_a_backup(monkeypatch):
    for value, expected in (("open_er_api", True), (" OPEN_ER_API ", True), ("", False), ("something_else", False)):
        monkeypatch.setattr(settings, "CURRENCY_FALLBACK_PROVIDER", value)
        assert (svc._fallback_provider() is not None) is expected
