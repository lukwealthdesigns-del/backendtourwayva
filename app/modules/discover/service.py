"""
DiscoverService (Master Blueprint §10-12) — runs the Discover LangGraph workflow
(app/modules/discover/graph.py) with real ports:

    constraints (request + saved profile) → cache → [rate → AI candidates →
    verification → scoring/validation] → personalization → persistence

Two things every response is explicit about, since none of this is a live
pricing feed:
  - every CostBreakdown carries source="estimated" (AI estimate, proportionally
    split) and excludes international flights (stated in `notes`);
  - a candidate that fails geocoding or names the wrong country is dropped, never
    shown with invented coordinates.

The budget is the TOTAL for the whole group; affordability is judged in the
budget currency with ONE real exchange rate — if none is available the search
fails honestly rather than comparing dollars to naira.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FeatureFlag
from app.core.exceptions import ProviderUnavailableError, ValidationAppError
from app.core.logging import get_logger
from app.db.models.discover import DiscoveryResult, DiscoverySearch
from app.modules.activities.service import ActivityService
from app.modules.currency.service import CurrencyService
from app.modules.entitlements.service import EntitlementService
from app.modules.discover.graph import DiscoverPorts, DiscoverState, build_discover_spec
from app.modules.discover.schemas import DiscoverResultItem, DiscoverSearchRequest, DiscoverSearchResponse
from app.modules.geocoding.service import GeocodingService
from app.modules.images.service import ImageService
from app.modules.planning.service import Runner
from app.modules.planning.workflow import WorkflowSpec, run_workflow
from app.modules.weather.service import WeatherService
from app.providers.llm.interface import LLMProvider
from app.providers.llm.openai_provider import OpenAIProvider
from app.repositories.discover_repository import DiscoverRepository
from app.repositories.memory_repository import MemoryRepository
from app.repositories.preferences_repository import PreferencesRepository
from app.repositories.travel_history_repository import TravelHistoryRepository
from app.services.cache_service import CacheService

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 60 * 60 * 6   # long enough to absorb repeat searches, short enough to stay fresh
_UNAVAILABLE_CODES = {"ai_unavailable", "currency_unavailable"}


async def _langgraph_runner(spec: WorkflowSpec, initial: dict[str, Any]) -> dict[str, Any]:
    return await run_workflow(spec, DiscoverState, initial)


class DiscoverService:
    def __init__(self, db: AsyncSession, *, llm: Optional[LLMProvider] = None, runner: Optional[Runner] = None):
        self.db = db
        self.repo = DiscoverRepository(db)
        self.llm: LLMProvider = llm or OpenAIProvider()
        self._runner: Runner = runner or _langgraph_runner

    async def search(self, *, user_id: uuid.UUID, payload: DiscoverSearchRequest) -> DiscoverSearchResponse:
        ports = self._build_ports(user_id, payload)
        state = await self._runner(build_discover_spec(ports), {"request": payload.model_dump(mode="json")})

        error = state.get("error")
        if error:
            if error["code"] in _UNAVAILABLE_CODES:
                raise ProviderUnavailableError(error["message"])
            raise ValidationAppError(error["message"], details={"code": error["code"]})

        outcome = state["outcome"]
        return DiscoverSearchResponse(
            search_id=outcome["search_id"],
            results=[DiscoverResultItem(**r) for r in outcome["results"]],
            source=outcome["source"],
            notes=outcome["notes"],
        )

    # ------------------------------------------------------------------
    def _build_ports(self, user_id: uuid.UUID, payload: DiscoverSearchRequest) -> DiscoverPorts:
        geocoding, weather, currency = GeocodingService(), WeatherService(), CurrencyService()
        images, activities = ImageService(), ActivityService()

        async def llm(messages: list[dict[str, str]], temperature: float, max_tokens: int) -> str:
            response = await self.llm.generate(messages, temperature=temperature, max_tokens=max_tokens)
            try:
                from app.modules.analytics.service import AnalyticsService

                await AnalyticsService(self.db).record_ai_usage(
                    user_id=user_id, feature="discover", model_used=response.model_used,
                    prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens,
                    used_fallback=response.used_fallback,
                )
            except Exception as exc:  # noqa: BLE001 - metering must never break a search
                await self.db.rollback()
                logger.warning("discover_usage_record_failed", error=str(exc))
            return response.content

        async def geocode(query: str) -> Any:
            return await geocoding.forward_geocode(query)

        async def forecast(latitude: float, longitude: float, days: int) -> list[dict[str, Any]]:
            response = await weather.get_forecast(latitude, longitude, days=days)
            return [d.model_dump(mode="json") for d in response.days]

        async def image(destination: str) -> Optional[dict[str, str]]:
            found = await images.get_or_search(destination)
            return {"url": found.url, "attribution": f"Photo by {found.photographer_name} on Unsplash"}

        async def nearby_activities(latitude: float, longitude: float) -> list[str]:
            response = await activities.search(latitude=latitude, longitude=longitude, radius_km=10)
            return [a.name for a in response.results[:5]]

        async def usd_rate(target: str) -> float:
            return (await currency.get_rate("USD", target)).rate

        async def load_profile() -> dict[str, Any]:
            prefs = await PreferencesRepository(self.db).get(user_id)
            memories = (
                await MemoryRepository(self.db).list_for_user(user_id, enabled_only=True)
                if await EntitlementService(self.db).has_feature(user_id, FeatureFlag.MEMORY) else []
            )
            visited = await TravelHistoryRepository(self.db).list_destinations_for_user(user_id)
            profile: dict[str, Any] = {
                "memories": [m.content for m in memories][:5],
                "visited": [v.destination for v in visited],
            }
            if prefs is not None:
                profile.update(
                    interests=list(prefs.interests or []),
                    travel_style=", ".join(prefs.travel_styles or []) or None,
                    accommodation_preference=prefs.accommodation_preference,
                    transportation_preference=prefs.transportation_preference,
                )
            return profile

        async def cache_get(key: str) -> Optional[list[dict[str, Any]]]:
            return await CacheService.get_json(key)

        async def persist(results: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
            search = await self.repo.create_search(
                DiscoverySearch(user_id=user_id, request_params=payload.model_dump(mode="json"))
            )
            for item in results:
                await self.repo.create_result(DiscoveryResult(
                    search_id=search.id, destination=item["destination"], country=item.get("country"),
                    latitude=item.get("latitude"), longitude=item.get("longitude"),
                    estimated_total_cost=item["cost_breakdown"]["total"],
                    estimated_cost_currency=item["cost_breakdown"]["currency"],
                    score=item.get("score") or 0.0,
                    reasons=(item.get("reasons") or "")[:1000] or None, extra_data=item,
                ))
            await self.db.commit()
            if meta["source"] == "live" and meta.get("cache_payload"):
                await CacheService.set_json(meta["cache_key"], meta["cache_payload"], _CACHE_TTL_SECONDS)
            return {"search_id": search.id, "results": results, "source": meta["source"], "notes": meta["notes"]}

        return DiscoverPorts(
            llm=llm, geocode=geocode, forecast=forecast, image=image, activities=nearby_activities,
            usd_rate=usd_rate, load_profile=load_profile, cache_get=cache_get, persist=persist,
        )
