"""
ApproximateLocationService — the approximate location behind the dashboard weather widget
(Master Blueprint §8, §9, §84).

Resolution order (each step only if the previous produced no coordinates):
  1. The request's IP address, via IPinfo (city-level coordinates)      -> source "ip"
  2. The IP's city/region/country geocoded (IPinfo answered but had no coordinates) -> source "ip"
  3. The country the account was localized to, geocoded                 -> source "account_country"
  4. Nothing usable -> LocationUnavailableError (client asks for device location / a typed place)

Privacy: this runs per request and is never stored or tracked over time; the raw IP is never
returned; an explicit latitude/longitude from the client always overrides it (handled by callers).
It never invents a location: geocoding failures degrade to the next step, and the last step is an
explicit error.
"""
from __future__ import annotations

from typing import Optional

from app.core.country_data import get_country
from app.core.exceptions import LocationUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.db.models.user import User
from app.modules.geocoding.service import GeocodingService
from app.modules.location.schemas import ApproximateLocationResponse
from app.providers.geolocation.ipinfo_provider import IPinfoProvider

logger = get_logger(__name__)


class ApproximateLocationService:
    def __init__(self) -> None:
        self._ipinfo = IPinfoProvider()
        self._geocoding = GeocodingService()

    async def resolve(self, *, user: User, client_ip: Optional[str]) -> ApproximateLocationResponse:
        ip_location = await self._ipinfo.lookup_location(client_ip)

        # 1. IP address with coordinates
        if ip_location and ip_location.latitude is not None and ip_location.longitude is not None:
            return self._response(
                latitude=ip_location.latitude, longitude=ip_location.longitude,
                city=ip_location.city, region=ip_location.region, country=ip_location.country,
                timezone=ip_location.timezone, source="ip",
            )

        # 2. IP address without coordinates: geocode what it did tell us
        if ip_location and (ip_location.city or ip_location.country):
            query = self._query(ip_location.city, ip_location.region, ip_location.country)
            found = await self._geocode(query)
            if found is not None:
                return self._response(
                    latitude=found[0], longitude=found[1], city=ip_location.city, region=ip_location.region,
                    country=ip_location.country, timezone=ip_location.timezone, source="ip",
                )

        # 3. The country the account was localized to at signup
        country = (user.country or "").upper() or None
        if country:
            found = await self._geocode(self._query(None, None, country))
            if found is not None:
                info = get_country(country)
                return self._response(
                    latitude=found[0], longitude=found[1], city=None, region=None, country=country,
                    timezone=(info.timezone if info else user.timezone), source="account_country",
                )

        raise LocationUnavailableError(
            "We couldn't work out your location. Allow location access in your browser, or send "
            "latitude and longitude.",
        )

    @staticmethod
    def _query(city: Optional[str], region: Optional[str], country_code: Optional[str]) -> str:
        info = get_country(country_code or "")
        country_name = info.name if info else (country_code or "")
        return ", ".join(part for part in (city, region, country_name) if part)

    async def _geocode(self, query: str) -> Optional[tuple[float, float]]:
        if not query:
            return None
        try:
            result = await self._geocoding.forward_geocode(query)
            return result.latitude, result.longitude
        except Exception as exc:  # noqa: BLE001 - provider unavailable / not found: try the next step
            logger.warning("approximate_location_geocode_failed", error=redact_secrets(exc))
            return None

    @staticmethod
    def _response(*, latitude, longitude, city, region, country, timezone, source) -> ApproximateLocationResponse:
        info = get_country(country or "")
        return ApproximateLocationResponse(
            latitude=latitude, longitude=longitude, city=city, region=region, country=country,
            country_name=info.name if info else None, timezone=timezone, source=source,
        )
