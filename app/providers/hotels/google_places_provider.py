"""
Google Places hotel DISCOVERY (Places API (New)).

Answers "which hotels are there, where, how are they rated, what do they look like". It does NOT know rooms,
availability or prices and never claims to: every result has kind="discovery" and price_total=None. Live rooms and
prices come only from an inventory provider (Amadeus today; Booking.com / Expedia later).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.core.config import settings
from app.providers.google_maps.client import google_request
from app.providers.google_maps.parsing import PLACE_FIELDS, PLACES_BASE, parse_place, parse_places, photo_path
from app.providers.hotels.interface import HotelDiscoveryProvider, HotelOffer

_RADIUS_M = 20000.0


def photo_url_for(photo_name: Optional[str]) -> Optional[str]:
    return photo_path(f"{settings.API_V1_PREFIX}/places/photo", settings.SECRET_KEY, photo_name)


def to_offer(place: dict[str, Any], *, check_in: Optional[date] = None, check_out: Optional[date] = None) -> HotelOffer:
    return HotelOffer(
        hotel_id=f"gp:{place['place_id']}", hotel_name=place["name"], offer_id=None,
        latitude=place["latitude"], longitude=place["longitude"], check_in=check_in, check_out=check_out,
        room_description=place.get("summary"), price_total=None, currency=None, provider="google_places",
        kind="discovery", provider_reference=place["place_id"], address=place.get("address"),
        rating=place.get("rating"), user_rating_count=place.get("user_rating_count"),
        photo_url=photo_url_for(place.get("photo_name")), website=place.get("website"),
        maps_url=place.get("maps_url"), price_level=place.get("price_level"),
    )


class GooglePlacesHotelProvider(HotelDiscoveryProvider):
    async def discover_hotels(
        self, *, latitude: float, longitude: float, destination: str, check_in: Optional[date] = None,
        check_out: Optional[date] = None, max_hotels: int = 20,
    ) -> list[HotelOffer]:
        body = {
            "textQuery": f"hotels in {destination}",
            "includedType": "lodging", "strictTypeFiltering": True,
            "pageSize": max(1, min(max_hotels, 20)),
            "locationBias": {"circle": {"center": {"latitude": latitude, "longitude": longitude}, "radius": _RADIUS_M}},
        }
        data = await google_request("google_places", "POST", f"{PLACES_BASE}/places:searchText", field_mask=PLACE_FIELDS, body=body)
        return [to_offer(p, check_in=check_in, check_out=check_out) for p in parse_places(data)]

    async def get_hotel(self, *, reference: str) -> Optional[HotelOffer]:
        mask = PLACE_FIELDS.replace("places.", "")
        data = await google_request("google_places", "GET", f"{PLACES_BASE}/places/{reference}", field_mask=mask)
        place = parse_place(data)
        return to_offer(place) if place else None
