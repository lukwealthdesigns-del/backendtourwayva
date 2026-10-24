"""
Mapbox Geocoding API v6 as a GeocodingProvider.

Docs: https://docs.mapbox.com/api/search/geocoding/

Two rules from Mapbox's terms shape this class (see app/core/location_policy.py):
  * Autocomplete (`search_places`) is always TEMPORARY: it is for display while typing and must never be cached or stored.
  * Forward/reverse lookups that callers keep (a trip's destination, a saved place) must be PERMANENT (`permanent=true`,
    billed separately, card on file). `permanent` is therefore an explicit constructor argument, set from
    MAPBOX_GEOCODING_PERMANENT; the geocoding service only puts a Mapbox provider in the "keep" chain when it is true.
Mapbox v6 returns no points of interest, so `precise=True` accepts only confident street addresses and otherwise reports
"not found", letting a POI-capable provider (OpenCage/OSM) answer for a venue name instead of pinning it to a city centre.
"""
from __future__ import annotations

import httpx

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.geocoding.interface import GeocodeResult, GeocodingProvider
from app.providers.http_resilience import safe_error_text
from app.providers.mapbox import client
from app.providers.mapbox.parsing import PLACE_FEATURE_TYPES, parse_geocode_features

logger = get_logger(__name__)

_PLACE_TYPES_PARAM = ",".join(sorted(PLACE_FEATURE_TYPES))
_MAX_QUERY_CHARS, _MAX_QUERY_WORDS = 256, 20


def clean_query(query: str) -> str:
    """Mapbox rejects queries over 256 characters or 20 words, and any semicolon: trim instead of failing."""
    words = query.replace(";", " ").split()[:_MAX_QUERY_WORDS]
    return " ".join(words)[:_MAX_QUERY_CHARS].strip()


class MapboxGeocodingProvider(GeocodingProvider):
    def __init__(self, *, permanent: bool = False, precise: bool = False) -> None:
        self.permanent = permanent
        self.precise = precise

    async def search_places(self, query: str, limit: int = 6) -> list[GeocodeResult]:
        q = clean_query(query)
        if len(q) < 2:
            return []
        data = await self._get("forward", {"q": q, "autocomplete": "true", "types": _PLACE_TYPES_PARAM,
                                           "limit": min(10, max(1, limit)), "permanent": "false"})
        return parse_geocode_features(data, limit)

    async def forward_geocode(self, query: str) -> GeocodeResult:
        q = clean_query(query)
        if not q:
            raise NotFoundError("No matching location found.")
        params = {"q": q, "autocomplete": "false", "limit": 5, "permanent": str(self.permanent).lower(),
                  "types": "address" if self.precise else _PLACE_TYPES_PARAM}
        found = parse_geocode_features(await self._get("forward", params), 1, precise=self.precise)
        if not found:
            raise NotFoundError("No matching location found.")
        return found[0]

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodeResult:
        data = await self._get("reverse", {"longitude": f"{longitude:.6f}", "latitude": f"{latitude:.6f}",
                                           "permanent": str(self.permanent).lower()})
        found = parse_geocode_features(data, 1, any_type=True)
        if not found:
            raise NotFoundError("No matching location found.")
        return found[0]

    async def _get(self, endpoint: str, params: dict) -> dict:
        try:
            return await client.get_json("mapbox_geocoding", f"{client.GEOCODE_BASE}/{endpoint}", params)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 422:   # Mapbox rejected the query itself (bad characters, too long): nothing to find, not an outage
                raise NotFoundError("No matching location found.") from exc
            logger.error("mapbox_geocoding_http_error", status=status, body=safe_error_text(exc))
            raise ProviderUnavailableError("Geocoding lookup failed. Please try again.") from exc
        except (CircuitOpenError, ProviderUnavailableError):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("mapbox_geocoding_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Geocoding lookup failed. Please try again.") from exc
