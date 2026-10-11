"""The application's resolver: capability -> adapters, driven by settings (see HOTEL_DISCOVERY_PROVIDERS etc.)."""
from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.providers.travel.resolver import Capability, ProviderResolver

_ORDER_SETTING = {
    Capability.HOTEL_DISCOVERY: "HOTEL_DISCOVERY_PROVIDERS",
    Capability.HOTEL_INVENTORY: "HOTEL_INVENTORY_PROVIDERS",
    Capability.ACTIVITY_DISCOVERY: "ACTIVITY_PROVIDERS",
    Capability.FLIGHT_SEARCH: "FLIGHT_PROVIDERS",
    Capability.FLIGHT_STATUS: "FLIGHT_STATUS_PROVIDERS",
    Capability.ROUTING: "ROUTING_PROVIDERS",
}

_resolver: Optional[ProviderResolver] = None


def _google() -> bool:
    return bool(settings.GOOGLE_MAPS_API_KEY)


def _amadeus() -> bool:
    return bool(settings.AMADEUS_CLIENT_ID and settings.AMADEUS_CLIENT_SECRET)


def _build() -> ProviderResolver:
    r = ProviderResolver(lambda cap: getattr(settings, _ORDER_SETTING[cap]))

    def hotels_google():
        from app.providers.hotels.google_places_provider import GooglePlacesHotelProvider
        return GooglePlacesHotelProvider()

    def hotels_amadeus():
        from app.providers.hotels.amadeus_provider import AmadeusHotelProvider
        return AmadeusHotelProvider()

    def activities_google():
        from app.providers.activities.google_places_provider import GooglePlacesActivityProvider
        return GooglePlacesActivityProvider()

    def activities_amadeus():
        from app.providers.activities.amadeus_provider import AmadeusActivityProvider
        return AmadeusActivityProvider()

    def flights_travelpayouts():
        from app.providers.flights.travelpayouts_provider import TravelpayoutsFlightProvider
        return TravelpayoutsFlightProvider()

    def flights_amadeus():
        from app.providers.flights.amadeus_provider import AmadeusFlightProvider
        return AmadeusFlightProvider()

    def status_aviationstack():
        from app.providers.flight_status.aviationstack_provider import AviationstackProvider
        return AviationstackProvider()

    def routes_google():
        from app.providers.maps.google_routes_provider import GoogleRoutesProvider
        return GoogleRoutesProvider()

    def routes_ors():
        from app.providers.maps.openrouteservice_provider import OpenRouteServiceProvider
        return OpenRouteServiceProvider()

    r.register(Capability.HOTEL_DISCOVERY, "google_places", is_configured=_google, factory=hotels_google)
    r.register(Capability.HOTEL_INVENTORY, "amadeus", is_configured=_amadeus, factory=hotels_amadeus)
    r.register(Capability.ACTIVITY_DISCOVERY, "google_places", is_configured=_google, factory=activities_google)
    r.register(Capability.ACTIVITY_DISCOVERY, "amadeus", is_configured=_amadeus, factory=activities_amadeus)
    r.register(Capability.FLIGHT_SEARCH, "travelpayouts", is_configured=lambda: bool(settings.TRAVELPAYOUTS_TOKEN), factory=flights_travelpayouts)
    r.register(Capability.FLIGHT_SEARCH, "amadeus", is_configured=_amadeus, factory=flights_amadeus)
    r.register(Capability.FLIGHT_STATUS, "aviationstack", is_configured=lambda: bool(settings.AVIATIONSTACK_API_KEY), factory=status_aviationstack)
    r.register(Capability.ROUTING, "google_routes", is_configured=_google, factory=routes_google)
    r.register(Capability.ROUTING, "openrouteservice", is_configured=lambda: bool(settings.ORS_API_KEY), factory=routes_ors)
    return r


def get_resolver() -> ProviderResolver:
    global _resolver
    if _resolver is None:
        _resolver = _build()
    return _resolver


def reset_resolver() -> None:
    """For tests and settings changes."""
    global _resolver
    _resolver = None
