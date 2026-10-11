"""
Data freshness and provenance for every externally sourced travel object.

Each object a provider returns carries WHERE it came from and HOW FRESH it is, so the AI, the UI and the budget
never present a cached or estimated value as a verified live one. Stdlib only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


class DataStatus(str, Enum):
    VERIFIED_LIVE = "VERIFIED_LIVE"        # fetched from the provider for this request
    VERIFIED_RECENT = "VERIFIED_RECENT"    # verified a short while ago, still inside its freshness window
    ESTIMATED = "ESTIMATED"                # an estimate (e.g. a price level), never a quoted price
    CACHED = "CACHED"                      # served from our cache or a provider's cached data
    STALE = "STALE"                        # past its freshness window; show only with a warning
    UNAVAILABLE = "UNAVAILABLE"            # the provider could not supply it


def provenance(
    provider: str, reference: Optional[str], status: DataStatus, *,
    ttl_seconds: Optional[int] = None, source_url: Optional[str] = None, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The standard provenance fields: provider, provider_reference, data_status, last_verified_at, expires_at, source_url."""
    verified_at = now or datetime.now(timezone.utc)
    return {
        "provider": provider,
        "provider_reference": reference,
        "data_status": status.value,
        "last_verified_at": verified_at.isoformat(),
        "expires_at": (verified_at + timedelta(seconds=ttl_seconds)).isoformat() if ttl_seconds else None,
        "source_url": source_url,
    }


def downgrade_for_cache(record: dict[str, Any]) -> dict[str, Any]:
    """A record read back from our cache is CACHED unless it was already something weaker."""
    if record.get("data_status") in (DataStatus.VERIFIED_LIVE.value, DataStatus.VERIFIED_RECENT.value):
        return {**record, "data_status": DataStatus.CACHED.value}
    return record


def is_expired(record: dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    raw = record.get("expires_at")
    if not raw:
        return False
    try:
        expires = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) >= expires
