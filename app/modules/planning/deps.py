"""Real ports for the generation workflow: the existing, cached, provider-
abstracted services (geocoding, Amadeus hotels/activities, weather, currency),
the LLM provider, and the database writer."""
from __future__ import annotations

import uuid
from typing import Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.activities.service import ActivityService
from app.modules.currency.service import CurrencyService
from app.modules.geocoding.service import GeocodingService
from app.modules.hotels.service import HotelService
from app.modules.images.service import ImageService
from app.modules.planning.domain import GeoPoint
from app.modules.planning.graph import PlanningPorts
from app.modules.planning.persistence import PlanningPersistence
from app.modules.weather.service import WeatherService
from app.providers.llm.interface import LLMProvider
from app.providers.llm.openai_provider import OpenAIProvider
from app.providers.opening_hours.factory import get_opening_hours_provider

logger = get_logger(__name__)

_ACTIVITY_RADIUS_KM = 10
_MAX_HOTELS = 20


def build_ports(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    trip_id: uuid.UUID,
    llm: Optional[LLMProvider] = None,
    premium_ai: bool = False,
    tier: Optional[str] = None,
    day_offset: int = 0,
    partial: bool = False,
    after_save: Optional[Callable[[Any, Any], None]] = None,
) -> PlanningPorts:
    """`premium_ai` (the PREMIUM_AI feature) selects the strong model tier for planning;
    everyone else plans on the default model. `tier` overrides that choice (long trips: only the first detailed part
    uses the strong tier). `day_offset`/`partial`/`after_save` write one part of a long trip (see PlanningPersistence)."""
    geocoding, hotels = GeocodingService(), HotelService()
    activities, weather, currency, images = ActivityService(), WeatherService(), CurrencyService(), ImageService()
    provider = llm or OpenAIProvider()
    hours_provider = get_opening_hours_provider()

    async def geocode(query: str) -> GeoPoint:
        result = await geocoding.forward_geocode(query)
        return GeoPoint(
            latitude=result.latitude, longitude=result.longitude,
            formatted_address=result.formatted_address, country=result.country, city=result.city,
        )

    async def search_hotels(city_code: str, check_in, check_out, adults: int) -> list[dict[str, Any]]:
        response = await hotels.search(
            city_code=city_code, check_in=check_in, check_out=check_out, adults=adults, max_hotels=_MAX_HOTELS
        )
        return [r.model_dump() for r in response.results]

    async def resolve_city_code(destination: str) -> Optional[str]:
        return await hotels.resolve_city_code(destination)

    async def hotel_image(hotel_name: str) -> Optional[dict[str, str]]:
        if not hotel_name:
            return None
        found = await images.get_or_search(hotel_name)
        return {"url": found.url, "attribution": f"Photo by {found.photographer_name} on Unsplash"}

    async def search_activities(latitude: float, longitude: float) -> list[dict[str, Any]]:
        response = await activities.search(latitude=latitude, longitude=longitude, radius_km=_ACTIVITY_RADIUS_KM)
        return [r.model_dump() for r in response.results]

    async def forecast(latitude: float, longitude: float, days: int) -> list[dict[str, Any]]:
        response = await weather.get_forecast(latitude, longitude, days)
        return [d.model_dump() for d in response.days]

    async def opening_hours(name: str, latitude: float, longitude: float) -> Optional[str]:
        if hours_provider is None:
            return None
        found = await hours_provider.lookup(name=name, latitude=latitude, longitude=longitude)
        return found.raw if found else None

    async def convert_rate(base: str, target: str) -> float:
        return (await currency.get_rate(base, target)).rate

    def make_llm(model_tier: str, feature: str):
        async def llm_call(messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
            response = await provider.generate(
                messages, temperature=temperature, max_tokens=max_tokens,
                tier=model_tier, timeout=settings.AI_PLANNING_TIMEOUT_SECONDS,
            )
            try:
                from app.modules.analytics.service import AnalyticsService

                await AnalyticsService(db).record_ai_usage(
                    user_id=user_id, feature=feature, model_used=response.model_used,
                    prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens,
                    used_fallback=response.used_fallback, trip_id=trip_id,
                )
            except Exception as exc:  # noqa: BLE001 - metering must never break planning
                await db.rollback()
                logger.warning("planning_usage_record_failed", error=str(exc))
            return response.content

        return llm_call

    llm_call = make_llm(tier or ("strong" if premium_ai else "default"), "itinerary_generation")
    llm_fast_call = make_llm("fast", "itinerary_outline")       # cheaper model for the long-trip route outline

    return PlanningPorts(
        geocode=geocode, search_hotels=search_hotels, search_activities=search_activities, forecast=forecast,
        convert_rate=convert_rate, llm=llm_call,
        persist=PlanningPersistence(db, trip_id=trip_id, actor_id=user_id, day_offset=day_offset, partial=partial, after_save=after_save),
        llm_fast=llm_fast_call,
        resolve_city_code=resolve_city_code, image=hotel_image,
        opening_hours=opening_hours, opening_hours_enabled=hours_provider is not None,
        max_opening_hours_lookups=settings.OPENING_HOURS_MAX_LOOKUPS_PER_PLAN,
        opening_hours_timeout_seconds=float(settings.OPENING_HOURS_TOTAL_TIMEOUT_SECONDS),
    )
