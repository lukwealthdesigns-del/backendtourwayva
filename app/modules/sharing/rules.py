"""
Pure sharing rules (stdlib only, unit-tested without the web stack).

  * the link token: 256 bits, only its hash is stored, revocable, optionally expiring
  * what a PUBLIC viewer may see: an explicit whitelist. Anything not listed here (budget, costs, notes, booking links,
    provider ids, owner identity, members, comments) is never part of a public page, however the trip model changes later.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

MAX_EXPIRY_DAYS = 365
REPORT_REASONS = ("spam", "inappropriate", "private_info", "other")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,96}$")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def looks_like_token(token: Optional[str]) -> bool:
    return bool(token) and bool(_TOKEN_RE.match(token))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clamp_expiry_days(days: Optional[int]) -> Optional[int]:
    """None (no expiry) stays None; otherwise 1..365."""
    if days is None:
        return None
    return max(1, min(int(days), MAX_EXPIRY_DAYS))


def expiry_from(now: datetime, days: Optional[int]) -> Optional[datetime]:
    d = clamp_expiry_days(days)
    return now + timedelta(days=d) if d else None


def link_state(*, revoked_at: Optional[datetime], expires_at: Optional[datetime], now: datetime) -> str:
    """'revoked' wins over 'expired' wins over 'active'."""
    if revoked_at is not None:
        return "revoked"
    if expires_at is not None and expires_at <= now:
        return "expired"
    return "active"


# ---- the public view: a whitelist, built field by field ----
_PUBLIC_ITEM = ("item_type", "title", "description", "location_name", "latitude", "longitude", "image_url")


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else (str(value) if value is not None else None)


def public_item(item: Any) -> dict[str, Any]:
    out = {k: getattr(item, k, None) for k in _PUBLIC_ITEM}
    out["start_time"] = _iso(getattr(item, "start_time", None))
    out["end_time"] = _iso(getattr(item, "end_time", None))
    return out


def public_trip(trip: Any, days: Iterable[tuple[Any, Iterable[Any]]]) -> dict[str, Any]:
    """The read-only view of a trip for anyone holding the link. NEVER includes the budget, any cost, notes, booking links,
    provider or external ids, the owner, the origin city, members or comments."""
    return {
        "title": trip.title,
        "destination": trip.destination,
        "start_date": _iso(trip.start_date),
        "end_date": _iso(trip.end_date),
        "travelers": trip.travelers,
        "overview": getattr(trip, "overview", None),
        "days": [
            {
                "day_number": day.day_number, "date": _iso(day.date), "weather_summary": getattr(day, "weather_summary", None),
                "items": [public_item(i) for i in sorted(items, key=lambda i: (getattr(i, "sort_order", 0) or 0))],
            }
            for day, items in days
        ],
    }
