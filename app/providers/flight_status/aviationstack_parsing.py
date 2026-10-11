"""Pure parsing for Aviationstack `/flights` responses. Stdlib only."""
from __future__ import annotations

from typing import Any, Optional

KNOWN_STATUSES = {"scheduled", "active", "landed", "cancelled", "incident", "diverted"}


def _endpoint(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    raw = raw or {}
    delay = raw.get("delay")
    return {
        "airport": raw.get("airport"), "iata": raw.get("iata"), "terminal": raw.get("terminal"), "gate": raw.get("gate"),
        "scheduled": raw.get("scheduled"), "estimated": raw.get("estimated"), "actual": raw.get("actual"),
        "delay_minutes": int(delay) if isinstance(delay, (int, float)) else None,
    }


def parse_status(data: dict[str, Any], flight_iata: str, flight_date: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The best matching record for the flight, or None. An error object or no data is None, never a guess."""
    if not isinstance(data, dict) or data.get("error"):
        return None
    wanted = flight_iata.upper()
    rows = [r for r in data.get("data") or [] if ((r.get("flight") or {}).get("iata") or "").upper() == wanted]
    if flight_date:
        dated = [r for r in rows if r.get("flight_date") == flight_date]
        rows = dated or rows
    if not rows:
        return None
    row = sorted(rows, key=lambda r: r.get("flight_date") or "", reverse=True)[0]
    status = (row.get("flight_status") or "").lower()
    return {
        "flight_iata": wanted,
        "airline": (row.get("airline") or {}).get("name"),
        "status": status if status in KNOWN_STATUSES else None,
        "flight_date": row.get("flight_date"),
        "departure": _endpoint(row.get("departure")),
        "arrival": _endpoint(row.get("arrival")),
    }
