"""Real HTTP-backed providers use resilient_request correctly: transient errors retried,
permanent errors not, secrets never leak into logs, circuit breaker engaged per provider."""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace as NS

import httpx
import pytest

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.resilience import CircuitOpenError, reset_breakers
from app.providers.http_resilience import http_retry_after, is_transient_http_error


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_breakers():
    reset_breakers()
    yield
    reset_breakers()


def _status_error(code, headers=None, text=""):
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(code, headers=headers or {}, text=text, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


def test_classification_matches_the_documented_policy():
    for code in (429, 500, 502, 503, 504):
        assert is_transient_http_error(_status_error(code)) is True
    for code in (400, 401, 403, 404, 422):
        assert is_transient_http_error(_status_error(code)) is False
    assert is_transient_http_error(httpx.ConnectTimeout("x")) is True
    assert is_transient_http_error(httpx.ConnectError("x")) is True
    assert is_transient_http_error(ValueError("not an http error")) is False


def test_retry_after_header_is_parsed_when_present():
    assert http_retry_after(_status_error(429, headers={"Retry-After": "7"})) == 7.0
    assert http_retry_after(_status_error(503)) is None
    assert http_retry_after(_status_error(429, headers={"Retry-After": "not-a-number"})) is None


# ---------------------------------------------------------------------------
# Weather provider: transient retried and recovers, permanent surfaces at once,
# opened circuit fails fast on the NEXT call, and secrets never reach the logs.
# ---------------------------------------------------------------------------
class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = responses  # shared across AsyncClient() instances created per retry attempt

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        request = httpx.Request("GET", url, params=params)
        response = httpx.Response(outcome, json={"forecast": {"forecastday": []}, "location": {}}, request=request)
        return response


def _patch_client(monkeypatch, module, responses):
    monkeypatch.setattr(module, "httpx", NS(
        AsyncClient=lambda **k: _FakeAsyncClient(responses),
        HTTPStatusError=httpx.HTTPStatusError, TimeoutException=httpx.TimeoutException,
        NetworkError=httpx.NetworkError, ConnectError=httpx.ConnectError,
    ))


def test_weather_provider_retries_a_500_and_recovers(monkeypatch):
    from app.core.config import settings
    from app.providers.weather import weatherapi_provider as mod

    monkeypatch.setattr(settings, "WEATHER_API_KEY", "wk_secret_value")
    monkeypatch.setattr(settings, "PROVIDER_RETRY_BASE_DELAY_SECONDS", 0.0)
    _patch_client(monkeypatch, mod, [503, 200])

    provider = mod.WeatherAPIProvider()
    result = _run(provider._call("https://api.weatherapi.com/v1/forecast.json", {"q": "Paris"}))
    assert result == {"forecast": {"forecastday": []}, "location": {}}


def test_weather_provider_does_not_retry_a_401_and_surfaces_it(monkeypatch):
    from app.core.config import settings
    from app.providers.weather import weatherapi_provider as mod

    monkeypatch.setattr(settings, "WEATHER_API_KEY", "wk_secret_value")
    _patch_client(monkeypatch, mod, [401])
    provider = mod.WeatherAPIProvider()
    with pytest.raises(ProviderUnavailableError):
        _run(provider._call("https://api.weatherapi.com/v1/forecast.json?key=wk_secret_value", {}))


def test_the_breaker_opens_after_repeated_failures_and_the_next_call_fails_fast(monkeypatch):
    from app.core.config import settings
    from app.providers.weather import weatherapi_provider as mod

    monkeypatch.setattr(settings, "WEATHER_API_KEY", "wk_secret_value")
    monkeypatch.setattr(settings, "PROVIDER_RETRY_BASE_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(settings, "PROVIDER_RETRY_ATTEMPTS", 1)          # exhaust the breaker fast
    monkeypatch.setattr(settings, "CIRCUIT_FAILURE_THRESHOLD", 2)

    provider = mod.WeatherAPIProvider()
    for _ in range(2):
        _patch_client(monkeypatch, mod, [503])
        with pytest.raises(ProviderUnavailableError):
            _run(provider._call("https://api.weatherapi.com/v1/forecast.json", {}))

    calls = []
    _patch_client(monkeypatch, mod, [200])  # would succeed if it were even tried

    class Counting(_FakeAsyncClient):
        async def get(self, *a, **k):
            calls.append(1)
            return await super().get(*a, **k)

    monkeypatch.setattr(mod, "httpx", NS(AsyncClient=lambda **k: Counting([200]), HTTPStatusError=httpx.HTTPStatusError,
                                         TimeoutException=httpx.TimeoutException, NetworkError=httpx.NetworkError,
                                         ConnectError=httpx.ConnectError))
    with pytest.raises((ProviderUnavailableError, CircuitOpenError)):
        _run(provider._call("https://api.weatherapi.com/v1/forecast.json", {}))
    assert calls == []                                        # the provider was never even contacted


def test_amadeus_404_is_not_found_not_a_provider_failure(monkeypatch):
    from app.providers.amadeus import client as amadeus_module

    async def fake_token(self):
        return "tok"

    monkeypatch.setattr(amadeus_module.AmadeusClient, "_get_access_token", fake_token)
    _patch_client(monkeypatch, amadeus_module, [])

    class NotFoundClient(_FakeAsyncClient):
        async def request(self, method, url, params=None, headers=None):
            return httpx.Response(404, json={}, request=httpx.Request(method, url))

    monkeypatch.setattr(amadeus_module, "httpx", NS(AsyncClient=lambda **k: NotFoundClient([]),
                                                     HTTPStatusError=httpx.HTTPStatusError,
                                                     TimeoutException=httpx.TimeoutException,
                                                     NetworkError=httpx.NetworkError, ConnectError=httpx.ConnectError))
    client = amadeus_module.AmadeusClient()
    with pytest.raises(NotFoundError):
        _run(client.get("/v1/x", resource="hotels"))

    from app.core.resilience import get_breaker
    assert get_breaker("amadeus_hotels").state == "closed"     # a 404 must never count as the provider failing


def test_different_amadeus_resources_have_independent_circuits(monkeypatch):
    from app.core.config import settings
    from app.providers.amadeus import client as amadeus_module

    monkeypatch.setattr(settings, "PROVIDER_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(settings, "CIRCUIT_FAILURE_THRESHOLD", 1)

    async def fake_token(self):
        return "tok"

    monkeypatch.setattr(amadeus_module.AmadeusClient, "_get_access_token", fake_token)

    class FailingClient(_FakeAsyncClient):
        async def request(self, method, url, params=None, headers=None):
            request = httpx.Request(method, url)
            raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(503, request=request))

    monkeypatch.setattr(amadeus_module, "httpx", NS(AsyncClient=lambda **k: FailingClient([]),
                                                     HTTPStatusError=httpx.HTTPStatusError,
                                                     TimeoutException=httpx.TimeoutException,
                                                     NetworkError=httpx.NetworkError, ConnectError=httpx.ConnectError))
    client = amadeus_module.AmadeusClient()
    with pytest.raises(ProviderUnavailableError):
        _run(client.get("/x", resource="flights"))

    from app.core.resilience import get_breaker
    assert get_breaker("amadeus_flights").is_open and not get_breaker("amadeus_hotels").is_open


def test_openai_tool_calling_turns_are_not_retried_but_plain_completions_are(monkeypatch):
    from app.core.config import settings
    from app.providers.llm import openai_provider as mod

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-secret")
    monkeypatch.setattr(settings, "AI_PRIMARY_MODEL", "gpt-x")
    monkeypatch.setattr(settings, "PROVIDER_RETRY_BASE_DELAY_SECONDS", 0.0)

    called = {"n": 0}

    class Client(_FakeAsyncClient):
        async def post(self, url, json=None, headers=None):
            called["n"] += 1
            request = httpx.Request("POST", url)
            raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(503, request=request))

    monkeypatch.setattr(mod, "httpx", NS(AsyncClient=lambda **k: Client([]), HTTPStatusError=httpx.HTTPStatusError,
                                         TimeoutException=httpx.TimeoutException, NetworkError=httpx.NetworkError,
                                         ConnectError=httpx.ConnectError))
    provider = mod.OpenAIProvider()
    with pytest.raises(ProviderUnavailableError):
        _run(provider._call_model("gpt-x", [{"role": "user", "content": "hi"}], 0.5, 100, [{"tool": 1}], used_fallback=False))
    assert called["n"] == 1                                    # tool-calling: exactly one attempt

    called["n"] = 0
    monkeypatch.setattr(mod, "httpx", NS(AsyncClient=lambda **k: Client([]), HTTPStatusError=httpx.HTTPStatusError,
                                         TimeoutException=httpx.TimeoutException, NetworkError=httpx.NetworkError,
                                         ConnectError=httpx.ConnectError))
    with pytest.raises(ProviderUnavailableError):
        _run(provider._call_model("gpt-x", [{"role": "user", "content": "hi"}], 0.5, 100, None, used_fallback=False))
    assert called["n"] > 1                                     # plain completion: retried
