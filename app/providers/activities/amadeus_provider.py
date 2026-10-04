"""
Amadeus activity provider (Master Blueprint §24) — Tours and
Activities API (GET /v1/shopping/activities), which is
coordinate-radius based rather than city-code based.
"""
from __future__ import annotations

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.providers.activities.interface import ActivityProvider, ActivityResult
from app.providers.amadeus.client import amadeus_client

logger = get_logger(__name__)


class AmadeusActivityProvider(ActivityProvider):
    async def search_activities(
        self, *, latitude: float, longitude: float, radius_km: int = 5
    ) -> list[ActivityResult]:
        try:
            data = await amadeus_client.get(
                "/v1/shopping/activities",
                params={"latitude": latitude, "longitude": longitude, "radius": radius_km},
                resource="activities",
            )
        except NotFoundError:
            return []

        return self._normalize(data.get("data", []))

    @staticmethod
    def _normalize(raw_results: list[dict]) -> list[ActivityResult]:
        results: list[ActivityResult] = []
        for entry in raw_results:
            price = entry.get("price", {})
            geo = entry.get("geoCode", {})
            pictures = entry.get("pictures", [])

            results.append(
                ActivityResult(
                    activity_id=entry.get("id", ""),
                    name=entry.get("name", "Unknown Activity"),
                    description=entry.get("shortDescription"),
                    latitude=geo.get("latitude", 0.0),
                    longitude=geo.get("longitude", 0.0),
                    price_amount=float(price["amount"]) if price.get("amount") else None,
                    currency=price.get("currencyCode"),
                    picture_url=pictures[0] if pictures else None,
                    booking_link=entry.get("bookingLink"),
                    provider="amadeus",
                )
            )
        return results
