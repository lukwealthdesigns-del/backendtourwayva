"""
LocalizationService — resolves country / currency / language / timezone
automatically at signup, per the Master Blueprint §8 signal priority:

    1. Explicit user preference   (not available yet — brand new signup)
    2. Account preference          (not available yet)
    3. Device/browser locale       (Accept-Language header, if provided)
    4. IP country (IPinfo)
    5. Phone-number region (fallback — always available at signup,
       since phone number is now a required signup field)
    6. Tour-Wayva default (en / USD / UTC)

COUNTRY is resolved by trying each signal in order until one yields a code: a region
subtag in Accept-Language (e.g. "en-US" -> "US") first, then IPinfo, then the phone
number's region, then the built-in default ("US"). CURRENCY and TIMEZONE always follow
the resolved country (via the 196-country dataset in app/core/country_data.py).
LANGUAGE is resolved separately and preferentially from Accept-Language's language
subtag (present far more often than its region subtag) even when a different signal
won country — a browser stating "fr" is a better language signal than a country's
single default language, since many countries are multilingual.

This is real, working logic (not a placeholder): if IPINFO_TOKEN is configured, it calls
IPinfo (via IPinfoProvider, Redis-cached) for an approximate country from the request
IP; otherwise, and always as a fallback, it derives country from the validated phone
number's region. It never claims exact GPS-level location (Blueprint §42 — IP Privacy).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.country_data import get_country
from app.providers.geolocation.ipinfo_provider import IPinfoProvider
from app.utils.accept_language import best_language, country_hint

_DEFAULT_COUNTRY = "US"
_DEFAULT_CURRENCY = "USD"
_DEFAULT_LANGUAGE = "en"
_DEFAULT_TIMEZONE = "UTC"

_ipinfo = IPinfoProvider()


@dataclass(frozen=True)
class ResolvedLocalization:
    country: str
    currency: str
    language: str
    timezone: str
    source: str  # "accept_language" | "ip" | "phone_region" | "default"


async def resolve_localization(
    phone_region_code: str,
    client_ip: Optional[str] = None,
    accept_language: Optional[str] = None,
) -> ResolvedLocalization:
    """Resolve localization fields for a brand-new signup."""
    country, source = country_hint(accept_language), "accept_language"

    if not country:
        country, source = await _ipinfo.lookup_country(client_ip), "ip"

    if not country:
        country, source = (phone_region_code or "").upper() or None, "phone_region"

    if not country:
        country, source = _DEFAULT_COUNTRY, "default"

    info = get_country(country)
    language = best_language(accept_language) or (info.language if info else _DEFAULT_LANGUAGE)

    return ResolvedLocalization(
        country=country,
        currency=info.currency if info else _DEFAULT_CURRENCY,
        language=language,
        timezone=info.timezone if info else _DEFAULT_TIMEZONE,
        source=source,
    )
