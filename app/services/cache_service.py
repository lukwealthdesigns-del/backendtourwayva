"""
CacheService — thin async Redis wrapper implementing the "reuse
before regenerate" principle (Master Blueprint §3, §59-61) for
Phase 2 external providers (geocoding, currency, weather).

Deterministic key design, per §60, e.g.:
    weather:{lat}:{lon}:{date}
    currency:{base}:{target}
    geocode:{normalized_query}

Each cache category gets its own TTL (never one global TTL, §61).
This is Redis-only for now (fast, simple, sufficient for Phase 2).
Durable normalized storage in PostgreSQL (e.g. geocoding_cache,
currency_cache tables) is a Phase 8 hardening item — Redis TTL expiry
already gives correct behavior today, just without a durable audit
trail of historical lookups.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: Optional[aioredis.Redis] = None


def _get_client() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


class CacheService:
    """Never raises on Redis unavailability — cache is a
    performance/cost optimization, not a correctness dependency.
    A cache miss (real or due to Redis being down) simply means the
    caller falls through to the live provider, per §78 graceful
    degradation.

    Every get_json/get_raw call also increments a Redis hit/miss
    counter keyed by the key's CATEGORY (the text before the first
    ':', e.g. "weather", "currency", "geocode", "hotel", "flight",
    "image", "route", "activity") — see cache_hit_rate_stats() below.
    This piggybacks on the existing deterministic key design (§60)
    instead of touching every call site individually, so instrumenting
    it costs one extra Redis command per lookup, not a rewrite of
    every provider service."""

    @staticmethod
    async def get_json(key: str) -> Optional[Any]:
        try:
            client = _get_client()
            raw = await client.get(key)
            value = json.loads(raw) if raw is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_get_failed", key=key, error=str(exc))
            return None
        await CacheService._record_outcome(key, hit=value is not None)
        return value

    @staticmethod
    async def set_json(key: str, value: Any, ttl_seconds: int) -> None:
        try:
            client = _get_client()
            await client.set(key, json.dumps(value), ex=ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_set_failed", key=key, error=str(exc))

    @staticmethod
    async def set_if_absent(key: str, value: str, ttl_seconds: int) -> Optional[bool]:
        """Atomic SET NX with expiry — used for locks and idempotency claims.
        Returns True if this caller set the key, False if it already existed,
        and None if Redis could not be reached (callers decide whether to
        proceed without the guard)."""
        try:
            client = _get_client()
            return bool(await client.set(key, value, ex=ttl_seconds, nx=True))
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_set_if_absent_failed", key=key, error=str(exc))
            return None

    @staticmethod
    async def get_raw(key: str) -> Optional[str]:
        try:
            value = await _get_client().get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_get_raw_failed", key=key, error=str(exc))
            return None
        await CacheService._record_outcome(key, hit=value is not None)
        return value

    @staticmethod
    async def set_raw(key: str, value: str, ttl_seconds: int) -> None:
        try:
            await _get_client().set(key, value, ex=ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_set_raw_failed", key=key, error=str(exc))

    @staticmethod
    async def delete(key: str) -> None:
        try:
            client = _get_client()
            await client.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_delete_failed", key=key, error=str(exc))

    @staticmethod
    async def list_keys(pattern: str) -> list[str]:
        """Non-blocking SCAN for keys matching `pattern`. For small, bounded key spaces
        (health snapshots, not user data) — never used for anything latency-sensitive."""
        try:
            client = _get_client()
            return [key async for key in client.scan_iter(match=pattern, count=100)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_list_keys_failed", pattern=pattern, error=str(exc))
            return []

    # --- Hit/miss instrumentation (Blueprint §58, §89: "cache hit rate", "cost savings") ---

    @staticmethod
    async def _record_outcome(key: str, *, hit: bool) -> None:
        # Skip our own stats/lock/idempotency keys and the Amadeus token
        # cache — only PROVIDER lookup categories count toward hit-rate
        # reporting (see cache-key builders below).
        category = key.split(":", 1)[0]
        if category in _NON_PROVIDER_CACHE_CATEGORIES:
            return
        try:
            client = _get_client()
            field = "hits" if hit else "misses"
            await client.hincrby(f"cache:stats:{category}", field, 1)
        except Exception as exc:  # noqa: BLE001
            # Stats are best-effort; never let counting a hit/miss break the
            # actual cache lookup that's already returned its result.
            logger.warning("cache_stat_record_failed", key=key, error=str(exc))

    @staticmethod
    async def cache_hit_rate_stats() -> dict[str, dict[str, int]]:
        """Per-category {hits, misses} since the counters were last reset
        (they live in Redis with no TTL — see reset_cache_hit_rate_stats).
        Used by GET /admin/analytics/cache for hit rate and estimated
        cost savings (Blueprint §58, §89)."""
        stats: dict[str, dict[str, int]] = {}
        try:
            client = _get_client()
            for category in _PROVIDER_CACHE_CATEGORIES:
                raw = await client.hgetall(f"cache:stats:{category}")
                hits = int(raw.get("hits", 0)) if raw else 0
                misses = int(raw.get("misses", 0)) if raw else 0
                if hits or misses:
                    stats[category] = {"hits": hits, "misses": misses}
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_stats_read_failed", error=str(exc))
        return stats

    @staticmethod
    async def reset_cache_hit_rate_stats() -> None:
        """Admin-triggered reset (e.g. after a deploy, or to start a fresh
        reporting window) — counters otherwise accumulate indefinitely."""
        try:
            client = _get_client()
            keys = [f"cache:stats:{c}" async for c in client.scan_iter(match="cache:stats:*", count=100)]
            if keys:
                await client.delete(*keys)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_stats_reset_failed", error=str(exc))


# --- Deterministic cache-key builders (Blueprint §60) ---

def geocode_cache_key(normalized_query: str) -> str:
    return f"geocode:{normalized_query}"


def opening_hours_cache_key(lat: float, lon: float, normalized_name: str) -> str:
    # 4 decimals ~ 11 m: two lookups for the same venue share an entry.
    return f"openinghours:{round(lat, 4)}:{round(lon, 4)}:{normalized_name}"


def reverse_geocode_cache_key(lat: float, lon: float) -> str:
    return f"geocode:reverse:{round(lat, 5)}:{round(lon, 5)}"


def currency_rate_cache_key(base: str, target: str) -> str:
    return f"currency:{base.upper()}:{target.upper()}"


def weather_current_cache_key(lat: float, lon: float) -> str:
    return f"weather:current:{round(lat, 3)}:{round(lon, 3)}"


def weather_forecast_cache_key(lat: float, lon: float, date: str) -> str:
    return f"weather:forecast:{round(lat, 3)}:{round(lon, 3)}:{date}"


def image_cache_key(entity: str, locale: str = "en") -> str:
    """Normalized per Blueprint §32, e.g. 'image:paris:eiffel_tower:en'."""
    normalized_entity = "_".join(entity.strip().lower().split())
    return f"image:{normalized_entity}:{locale.lower()}"


def route_cache_key(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, mode: str
) -> str:
    return (
        f"route:{mode}:{round(origin_lat, 4)},{round(origin_lon, 4)}"
        f"->{round(dest_lat, 4)},{round(dest_lon, 4)}"
    )


def hotel_search_cache_key(city_code: str, check_in: str, check_out: str, adults: int) -> str:
    return f"hotel:{city_code.upper()}:{check_in}:{check_out}:{adults}"


def flight_search_cache_key(
    origin: str, destination: str, departure_date: str, return_date: str | None, adults: int, cabin: str | None
) -> str:
    return f"flight:{origin.upper()}:{destination.upper()}:{departure_date}:{return_date or 'oneway'}:{adults}:{cabin or 'any'}"


def flight_offer_raw_cache_key(offer_id: str) -> str:
    """Caches the FULL raw Amadeus offer object (not the normalized
    FlightOffer) so it can be POSTed back to the Flight Offers Price
    endpoint for re-verification when the user actually adds it to a
    trip — Amadeus offers aren't re-fetchable by id the way hotels are,
    only re-priceable by resubmitting the whole object. Uses the
    "flight" category prefix too, so a raw-offer cache hit/miss still
    rolls up into the same admin cache-hit-rate reporting as flight
    searches."""
    return f"flight:offer_raw:{offer_id}"


def activity_search_cache_key(latitude: float, longitude: float, radius_km: int) -> str:
    return f"activity:{round(latitude, 3)}:{round(longitude, 3)}:{radius_km}"


# --- Category TTLs (seconds) — never one global TTL (Blueprint §61) ---
TTL_GEOCODING_SECONDS = 60 * 60 * 24 * 30   # stable geographic data: long TTL (30 days)
TTL_CURRENCY_SECONDS = 60 * 60              # short/medium TTL (1 hour)
TTL_WEATHER_CURRENT_SECONDS = 60 * 15       # short TTL (15 minutes)
TTL_WEATHER_FORECAST_SECONDS = 60 * 60 * 3  # short TTL (3 hours)
TTL_IMAGE_SECONDS = 60 * 60 * 24 * 60       # images rarely change: long TTL (60 days)
TTL_ROUTE_SECONDS = 60 * 60 * 24 * 7         # road networks are stable: long/medium TTL (7 days)
TTL_HOTEL_SEARCH_SECONDS = 60 * 5             # very short TTL (5 min) — availability/prices change fast
TTL_FLIGHT_SEARCH_SECONDS = 60 * 3            # very short TTL (3 min) — flight prices move fastest
TTL_FLIGHT_OFFER_RAW_SECONDS = 60 * 10        # slightly longer than search TTL: gives the user time to
                                               # pick a day and add it while the raw offer is still repriceable
TTL_OPENING_HOURS_SECONDS = 60 * 60 * 24 * 7          # a venue's hours rarely change: long TTL (7 days)
TTL_OPENING_HOURS_MISS_SECONDS = 60 * 60 * 24         # "no data found" is re-checked sooner (1 day)
TTL_ACTIVITY_SEARCH_SECONDS = 60 * 60 * 6     # medium TTL (6 hours) — activity listings are fairly stable

# --- Cache hit-rate instrumentation (Blueprint §58, §89) ---
# Every provider-lookup key category (the text before the first ':' —
# matches the builders above). "amadeus" (the OAuth token cache) and
# anything else outside this list is excluded from hit-rate reporting —
# see _NON_PROVIDER_CACHE_CATEGORIES.
_PROVIDER_CACHE_CATEGORIES = (
    "geocode", "currency", "weather", "image", "route", "hotel", "flight", "activity", "openinghours",
)
_NON_PROVIDER_CACHE_CATEGORIES = frozenset({"amadeus", "lock", "idempotency", "session_revoked"})

# Rough estimated USD cost of the provider call a cache HIT avoided, by
# category. Deliberately approximate (most of these providers don't bill
# per-call on the plans this project targets) — good enough for an
# order-of-magnitude "cache saved you about $X" admin metric, not an
# invoice. Kept next to _COST_PER_1K_TOKENS in analytics/service.py in
# spirit: a small, hand-maintained table rather than a live pricing API.
ESTIMATED_COST_SAVED_PER_HIT_USD: dict[str, float] = {
    "geocode": 0.0005,
    "currency": 0.0002,
    "weather": 0.0005,
    "image": 0.0001,   # Unsplash search is free, but still rate-limited — a hit avoided is a quota saved
    "route": 0.0005,
    "hotel": 0.003,
    "flight": 0.003,
    "activity": 0.001,
    "openinghours": 0.0001,   # Overpass is free; a hit spares a shared volunteer service one query
}
