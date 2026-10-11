"""
Pure parsing for Travelpayouts (Aviasales Data API) flight prices. Stdlib only.

These are CACHED prices gathered from real searches, useful as a guide and as an affiliate deep link. They are not a
live quote, so every offer built here is marked live_quote=False and the service labels it CACHED.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode

SITE = "https://www.aviasales.com"


def iso_duration(minutes: Optional[int]) -> str:
    """7h35m as 'PT7H35M' (the format the rest of the app uses)."""
    m = max(0, int(minutes or 0))
    h, rest = divmod(m, 60)
    return f"PT{h}H{rest}M" if h else f"PT{rest}M"


def booking_link(link: Optional[str], marker: str) -> Optional[str]:
    """The Aviasales search/deep link with the affiliate marker appended. None when there is no link."""
    if not link:
        return None
    url = link if link.startswith("http") else f"{SITE}{link}"
    if not marker:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode({'marker': marker})}"


def _arrival(departure_at: str, minutes: Optional[int]) -> str:
    """Departure plus the flight time, as an ISO instant (same UTC offset as the departure)."""
    try:
        dep = datetime.fromisoformat(departure_at.replace("Z", "+00:00"))
    except ValueError:
        return departure_at
    return (dep + timedelta(minutes=int(minutes or 0))).isoformat()


def parse_prices(data: dict[str, Any], *, adults: int, marker: str, limit: int = 20) -> list[dict[str, Any]]:
    """Travelpayouts `prices_for_dates` response -> neutral offer dicts (price is for ALL adults: the API prices one)."""
    currency = str(data.get("currency") or "usd").upper()
    out: list[dict[str, Any]] = []
    for row in data.get("data") or []:
        price, dep, origin, dest = row.get("price"), row.get("departure_at"), row.get("origin"), row.get("destination")
        if price is None or not dep or not origin or not dest:
            continue
        leg_minutes = row.get("duration_to") or row.get("duration")
        key = "|".join(str(row.get(k)) for k in ("origin", "destination", "departure_at", "return_at", "airline", "flight_number", "price"))
        out.append({
            "offer_id": "tp:" + hashlib.sha1(key.encode()).hexdigest()[:16],
            "origin": origin, "destination": dest, "departure_time": dep,
            "arrival_time": _arrival(dep, leg_minutes), "duration_iso8601": iso_duration(leg_minutes),
            "stops": int(row.get("transfers") or 0), "airline_codes": [row["airline"]] if row.get("airline") else [],
            "price_total": round(float(price) * max(1, adults), 2), "currency": currency,
            "booking_link": booking_link(row.get("link"), marker),
        })
        if len(out) >= limit:
            break
    return out
