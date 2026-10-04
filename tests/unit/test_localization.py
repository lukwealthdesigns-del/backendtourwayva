"""Country dataset, Accept-Language parsing, localization signal priority, IPinfo caching,
and suspicious-login detection."""
from __future__ import annotations

import asyncio

import pytest

from app.core.country_data import COUNTRIES, get_country
from app.modules.security.suspicious_login import is_suspicious
from app.utils import localization as localization_module
from app.utils.accept_language import best_language, country_hint, parse_accept_language


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Country dataset
# ---------------------------------------------------------------------------
def test_the_dataset_covers_a_broad_range_of_countries_with_complete_fields():
    assert len(COUNTRIES) >= 190
    for code, info in COUNTRIES.items():
        assert len(code) == 2 and code.isupper()
        assert len(info.currency) == 3 and info.currency.isupper()
        assert 2 <= len(info.language) <= 3 and info.language.islower()
        assert "/" in info.timezone or info.timezone == "UTC"


def test_lookup_is_case_insensitive_and_missing_codes_return_none():
    assert get_country("ng") == get_country("NG") == get_country(" Ng ")
    assert get_country("ng").currency == "NGN"
    assert get_country("zz") is None and get_country("") is None


@pytest.mark.parametrize("code,currency,language", [
    ("US", "USD", "en"), ("GB", "GBP", "en"), ("FR", "EUR", "fr"), ("DE", "EUR", "de"),
    ("NG", "NGN", "en"), ("JP", "JPY", "ja"), ("BR", "BRL", "pt"), ("IN", "INR", "hi"),
    ("CN", "CNY", "zh"), ("ZA", "ZAR", "en"), ("MX", "MXN", "es"), ("AE", "AED", "ar"),
])
def test_major_countries_have_correct_currency_and_language(code, currency, language):
    info = get_country(code)
    assert (info.currency, info.language) == (currency, language)


# ---------------------------------------------------------------------------
# Accept-Language parsing
# ---------------------------------------------------------------------------
def test_parses_multiple_tags_sorted_by_quality():
    tags = parse_accept_language("fr-CA,fr;q=0.8,en-US;q=0.6,en;q=0.4")
    assert [t.language for t in tags] == ["fr", "fr", "en", "en"]
    assert tags[0].region == "CA" and tags[2].region == "US"


def test_best_language_and_country_hint():
    assert best_language("fr-CA,en;q=0.5") == "fr" and country_hint("fr-CA,en;q=0.5") == "CA"
    assert best_language("en") == "en" and country_hint("en") is None       # no region subtag: no country signal
    assert best_language(None) is None and country_hint("") is None
    assert country_hint("en;q=0.9,pt-BR;q=0.8") == "BR"                     # falls through to the next tag with a region


def test_malformed_segments_are_skipped_not_raised():
    assert parse_accept_language("*, ,;;garbage,en-US;q=0.9") == parse_accept_language("en-US;q=0.9")


# ---------------------------------------------------------------------------
# resolve_localization: signal priority
# ---------------------------------------------------------------------------
@pytest.fixture()
def no_ipinfo(monkeypatch):
    async def lookup(self, client_ip):
        return None

    monkeypatch.setattr(localization_module.IPinfoProvider, "lookup_country", lookup)


def test_accept_language_region_wins_over_everything_else(no_ipinfo):
    result = _run(localization_module.resolve_localization("US", client_ip="1.2.3.4", accept_language="fr-CA"))
    assert (result.country, result.source) == ("CA", "accept_language")
    assert result.currency == "CAD" and result.language == "fr"             # explicit language kept, not CA's "en"


def test_ip_wins_when_accept_language_has_no_region(monkeypatch):
    async def lookup(self, client_ip):
        return "DE"

    monkeypatch.setattr(localization_module.IPinfoProvider, "lookup_country", lookup)
    result = _run(localization_module.resolve_localization("US", client_ip="1.2.3.4", accept_language="en"))
    assert (result.country, result.source) == ("DE", "ip")
    assert result.currency == "EUR" and result.language == "en"             # explicit "en" still wins over DE's "de"


def test_phone_region_is_the_fallback_when_ip_and_accept_language_give_nothing(no_ipinfo):
    result = _run(localization_module.resolve_localization("NG", client_ip=None, accept_language=None))
    assert (result.country, result.source) == ("NG", "phone_region")
    assert result.currency == "NGN" and result.language == "en"


def test_the_built_in_default_is_used_when_every_signal_is_missing(no_ipinfo):
    result = _run(localization_module.resolve_localization("", client_ip=None, accept_language=None))
    assert (result.country, result.currency, result.language, result.timezone, result.source) == (
        "US", "USD", "en", "America/New_York", "default")


def test_an_unrecognized_country_code_still_resolves_with_safe_defaults(no_ipinfo):
    result = _run(localization_module.resolve_localization("ZZ", client_ip=None, accept_language=None))
    assert result.country == "ZZ" and result.currency == "USD" and result.language == "en" and result.timezone == "UTC"


def test_a_language_only_accept_language_header_does_not_override_a_stronger_country_signal(monkeypatch):
    async def lookup(self, client_ip):
        return "JP"

    monkeypatch.setattr(localization_module.IPinfoProvider, "lookup_country", lookup)
    result = _run(localization_module.resolve_localization("US", client_ip="1.2.3.4", accept_language="es"))
    assert result.country == "JP" and result.language == "es"               # country from IP, language from browser


# ---------------------------------------------------------------------------
# IPinfo provider: Redis caching
# ---------------------------------------------------------------------------
@pytest.fixture()
def redis(monkeypatch):
    from app.services.cache_service import CacheService

    store = {}

    async def get_raw(key):
        return store.get(key)

    async def set_raw(key, value, ttl):
        store[key] = value

    monkeypatch.setattr(CacheService, "get_raw", staticmethod(get_raw))
    monkeypatch.setattr(CacheService, "set_raw", staticmethod(set_raw))
    return store


def test_ipinfo_provider_caches_a_successful_lookup(monkeypatch, redis):
    from app.core.config import settings
    from app.providers.geolocation.ipinfo_provider import IPinfoProvider

    monkeypatch.setattr(settings, "IPINFO_TOKEN", "tok")
    calls = []

    async def resilient(name, attempt, idempotent=True):
        calls.append(1)
        return {"country": "gb"}

    monkeypatch.setattr("app.providers.geolocation.ipinfo_provider.resilient_request", resilient)
    provider = IPinfoProvider()

    assert _run(provider.lookup_country("1.2.3.4")) == "GB"
    assert _run(provider.lookup_country("1.2.3.4")) == "GB"
    assert len(calls) == 1                                                  # second call served from cache
    assert redis["ipinfo:country:1.2.3.4"] == "GB"


def test_ipinfo_caches_a_negative_result_too_so_it_is_not_requeried(monkeypatch, redis):
    from app.core.config import settings
    from app.providers.geolocation.ipinfo_provider import IPinfoProvider

    monkeypatch.setattr(settings, "IPINFO_TOKEN", "tok")
    calls = []

    async def resilient(name, attempt, idempotent=True):
        calls.append(1)
        return {}                                                            # no country in the response

    monkeypatch.setattr("app.providers.geolocation.ipinfo_provider.resilient_request", resilient)
    provider = IPinfoProvider()
    assert _run(provider.lookup_country("5.6.7.8")) is None
    assert _run(provider.lookup_country("5.6.7.8")) is None
    assert len(calls) == 1 and redis["ipinfo:country:5.6.7.8"] == ""


def test_ipinfo_returns_none_without_a_token_or_ip(monkeypatch, redis):
    from app.core.config import settings
    from app.providers.geolocation.ipinfo_provider import IPinfoProvider

    monkeypatch.setattr(settings, "IPINFO_TOKEN", None)
    assert _run(IPinfoProvider().lookup_country("1.2.3.4")) is None
    monkeypatch.setattr(settings, "IPINFO_TOKEN", "tok")
    assert _run(IPinfoProvider().lookup_country(None)) is None


def test_a_provider_failure_degrades_to_none_never_raises(monkeypatch, redis):
    from app.core.config import settings
    from app.providers.geolocation.ipinfo_provider import IPinfoProvider

    monkeypatch.setattr(settings, "IPINFO_TOKEN", "tok")

    async def resilient(name, attempt, idempotent=True):
        raise RuntimeError("ipinfo down")

    monkeypatch.setattr("app.providers.geolocation.ipinfo_provider.resilient_request", resilient)
    assert _run(IPinfoProvider().lookup_country("1.2.3.4")) is None


# ---------------------------------------------------------------------------
# Suspicious-login detection (pure decision)
# ---------------------------------------------------------------------------
def test_a_different_country_on_a_returning_user_is_suspicious():
    assert is_suspicious(account_country="NG", login_country="US", is_first_login=False) is True


def test_the_same_country_is_never_suspicious():
    assert is_suspicious(account_country="NG", login_country="ng", is_first_login=False) is False


def test_the_first_login_is_never_flagged_even_from_a_different_country():
    assert is_suspicious(account_country="NG", login_country="US", is_first_login=True) is False


def test_unknown_countries_never_fabricate_a_signal():
    assert is_suspicious(account_country=None, login_country="US", is_first_login=False) is False
    assert is_suspicious(account_country="NG", login_country=None, is_first_login=False) is False


# ---------------------------------------------------------------------------
# Suspicious-login: the async wrapper (record + notify)
# ---------------------------------------------------------------------------
def test_check_and_record_flags_a_new_country_and_notifies_without_blocking_login(monkeypatch):
    from types import SimpleNamespace as NS

    from app.modules.security import suspicious_login

    events, notifications = [], []

    class FakeIPinfo:
        async def lookup_country(self, ip):
            return "US"

    class FakeSecurity:
        def __init__(self, db):
            pass

        async def record_event(self, **kwargs):
            events.append(kwargs)

    class FakeNotifications:
        def __init__(self, db):
            pass

        async def notify(self, **kwargs):
            notifications.append(kwargs)

    monkeypatch.setattr("app.providers.geolocation.ipinfo_provider.IPinfoProvider", FakeIPinfo)
    monkeypatch.setattr("app.modules.security.service.SecurityService", FakeSecurity)
    monkeypatch.setattr("app.modules.notifications.service.NotificationService", FakeNotifications)

    user = NS(id="u1", country="NG")
    result = _run(suspicious_login.check_and_record(db=None, user=user, ip_address="1.2.3.4", is_first_login=False))

    assert result == "US" and events[0]["metadata"] == {"account_country": "NG", "login_country": "US"}
    assert notifications[0]["send_email"] is True and "US" in notifications[0]["body"]


def test_check_and_record_does_nothing_when_not_suspicious(monkeypatch):
    from types import SimpleNamespace as NS

    from app.modules.security import suspicious_login

    class FakeIPinfo:
        async def lookup_country(self, ip):
            return "NG"

    monkeypatch.setattr("app.providers.geolocation.ipinfo_provider.IPinfoProvider", FakeIPinfo)
    user = NS(id="u1", country="NG")
    assert _run(suspicious_login.check_and_record(db=None, user=user, ip_address="1.2.3.4", is_first_login=False)) is None


def test_check_and_record_never_raises_on_an_ipinfo_failure(monkeypatch):
    from types import SimpleNamespace as NS

    from app.modules.security import suspicious_login

    class FakeIPinfo:
        async def lookup_country(self, ip):
            raise RuntimeError("down")

    monkeypatch.setattr("app.providers.geolocation.ipinfo_provider.IPinfoProvider", FakeIPinfo)
    user = NS(id="u1", country="NG")
    assert _run(suspicious_login.check_and_record(db=None, user=user, ip_address="1.2.3.4", is_first_login=False)) is None
