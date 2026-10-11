"""
Google Places attractions discovery (Places API (New), Nearby Search).

Finds attractions, museums, parks and landmarks near a point, with ratings and photos. It is DISCOVERY: it never
states a ticket price and never offers a booking link (a venue's website is information, not a booking).
"""
from __future__ import annotations

from app.core.config import settings
from app.providers.activities.interface import ActivityProvider, ActivityResult
from app.providers.google_maps.client import google_request
from app.providers.google_maps.parsing import PLACE_FIELDS, PLACES_BASE, parse_places
from app.providers.hotels.google_places_provider import photo_url_for

_TYPES = [
    "tourist_attraction", "museum", "park", "art_gallery", "historical_landmark", "amusement_park", "zoo", "aquarium",
]


class GooglePlacesActivityProvider(ActivityProvider):
    async def search_activities(self, *, latitude: float, longitude: float, radius_km: int = 5) -> list[ActivityResult]:
        body = {
            "includedTypes": _TYPES, "maxResultCount": 20, "rankPreference": "POPULARITY",
            "locationRestriction": {"circle": {"center": {"latitude": latitude, "longitude": longitude}, "radius": float(min(max(radius_km, 1), 50) * 1000)}},
        }
        data = await google_request("google_places", "POST", f"{PLACES_BASE}/places:searchNearby", field_mask=PLACE_FIELDS, body=body)
        return [
            ActivityResult(
                activity_id=p["place_id"], name=p["name"], description=p.get("summary"), latitude=p["latitude"],
                longitude=p["longitude"], price_amount=None, currency=None, picture_url=photo_url_for(p.get("photo_name")),
                booking_link=None, provider="google_places", rating=p.get("rating"),
                user_rating_count=p.get("user_rating_count"), address=p.get("address"), maps_url=p.get("maps_url"),
                website=p.get("website"),
            )
            for p in parse_places(data)
        ]
