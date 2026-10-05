"""Destination guides for ANY place, not just the curated ones.

Safety/accuracy design:
  * The place must exist: it is resolved through the geocoder first (404 for gibberish), and the guide is built for the
    resolved place, so the model cannot invent a destination.
  * The model's JSON is validated and CLAMPED field by field (lengths, ranges, ISO codes); anything unusable is dropped
    or rejected, never passed through.
  * It is clearly labelled as an AI overview with estimates (`source: "ai"` + a disclaimer), and the app shows it so.
  * One generation per place per 30 days (Redis), so the cost does not scale with users.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.modules.destinations.schemas import DestinationGuide
from app.modules.geocoding.service import GeocodingService
from app.modules.planning.llm_io import extract_json_object
from app.providers.llm.interface import LLMProvider
from app.providers.llm.openai_provider import OpenAIProvider
from app.services.cache_service import CacheService

logger = get_logger(__name__)

GUIDE_TTL_SECONDS = 60 * 60 * 24 * 30
_KEY = "destguide:v1:{place}"

_SYSTEM = (
    "You write short, factual travel guide data. Reply with ONE JSON object and nothing else, with exactly these keys: "
    '"overview" (1-2 sentences), "best_time" (typical best months, e.g. "Apr-Jun, Sep-Oct"), "suggested_days" (e.g. "3-5"), '
    '"daily_budget_usd" (integer: rough mid-range spend per person per day in US dollars, excluding flights), '
    '"highlights" (4 specific well-known attractions or experiences), "languages" (main spoken languages, short), '
    '"currency_code" (ISO 4217, 3 letters), "tips" (up to 2 short practical tips). '
    "Use only well-established general knowledge. If you are unsure of something, be general rather than specific. "
    "Never invent attractions, prices or opening hours."
)


def _text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:limit] or None


def _strings(value: Any, *, max_items: int, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        t = _text(v, limit)
        if t and t not in out:
            out.append(t)
        if len(out) >= max_items:
            break
    return out


def parse_guide(raw: str) -> dict[str, Any]:
    """Validate/clamp the model output. Raises ValueError when the essentials are missing."""
    data = extract_json_object(raw)
    overview = _text(data.get("overview"), 420)
    best_time = _text(data.get("best_time"), 80)
    days = _text(data.get("suggested_days"), 12)
    highlights = _strings(data.get("highlights"), max_items=6, limit=70)
    budget = data.get("daily_budget_usd")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget != budget:
        budget = None
    if not (overview and best_time and days and len(highlights) >= 3 and budget is not None):
        raise ValueError("The guide was incomplete.")
    currency = data.get("currency_code")
    currency = currency.upper() if isinstance(currency, str) and re.fullmatch(r"[A-Za-z]{3}", currency) else None
    return {
        "overview": overview, "best_time": best_time, "suggested_days": days,
        "daily_budget_usd": int(min(2000, max(10, round(budget)))),
        "highlights": highlights, "languages": _text(data.get("languages"), 80), "currency_code": currency,
        "tips": _strings(data.get("tips"), max_items=2, limit=120),
    }


class DestinationService:
    def __init__(self, llm: Optional[LLMProvider] = None, geocoder: Optional[GeocodingService] = None):
        self.llm = llm or OpenAIProvider()
        self.geocoder = geocoder or GeocodingService()

    async def guide(self, *, name: str, country: Optional[str]) -> DestinationGuide:
        query = f"{name}, {country}" if country else name
        try:
            place = await self.geocoder.forward_geocode(query)
        except NotFoundError as exc:
            raise NotFoundError("We couldn't find that place. Check the spelling or try a larger city nearby.") from exc

        key = _KEY.format(place=re.sub(r"[^a-z0-9]+", "-", place.formatted_address.lower()).strip("-")[:160])
        cached = await CacheService.get_json(key)
        if cached:
            return DestinationGuide(**{**cached, "cached": True})

        label = place.formatted_address
        try:
            response = await self.llm.generate(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": f"Place: {label}"}],
                temperature=0.3, max_tokens=600, tier="fast", timeout=40.0,
            )
            fields = parse_guide(response.content or "")
        except (ValueError, KeyError) as exc:
            logger.warning("destination_guide_unusable", place=label, error=str(exc))
            raise ProviderUnavailableError("Couldn't put the guide together just now. Please try again.") from exc

        guide = DestinationGuide(
            name=place.city or name.strip(), country=country or None, country_code=place.country,
            formatted_address=label, latitude=place.latitude, longitude=place.longitude, **fields,
        )
        await CacheService.set_json(key, guide.model_dump(), GUIDE_TTL_SECONDS)
        return guide
