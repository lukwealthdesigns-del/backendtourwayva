"""Trip version snapshots (Master Prompt §19): a COMPLETE, restorable copy of
the itinerary. Duck-typed on purpose (any object with the right attributes) so
the round trip is unit-testable without a database."""
from __future__ import annotations

import uuid
from datetime import time
from typing import Any, Optional

from app.core.constants import TripItemType

_ITEM_FIELDS = (
    "title", "description", "location_name", "latitude", "longitude", "estimated_cost", "currency",
    "provider", "source", "external_id", "booking_link", "image_url", "notes", "sort_order",
)


def item_to_snapshot(item: Any) -> dict[str, Any]:
    data = {name: getattr(item, name, None) for name in _ITEM_FIELDS}
    data["id"] = str(item.id) if getattr(item, "id", None) else None
    data["item_type"] = item.item_type.value if hasattr(item.item_type, "value") else str(item.item_type)
    data["start_time"] = item.start_time.isoformat() if item.start_time else None
    data["end_time"] = item.end_time.isoformat() if item.end_time else None
    return data


def build_snapshot(overview: Optional[str], days_with_items: list[tuple[Any, list[Any]]]) -> dict[str, Any]:
    return {
        "overview": overview,
        "days": [
            {
                "day_number": day.day_number,
                "date": day.date.isoformat(),
                "weather_summary": getattr(day, "weather_summary", None),
                "items": [item_to_snapshot(i) for i in items],
            }
            for day, items in days_with_items
        ],
    }


def snapshot_item_kwargs(snapshot_item: dict[str, Any], trip_day_id: uuid.UUID) -> dict[str, Any]:
    """Constructor kwargs for a TripItem restored from a snapshot entry.
    Tolerates snapshots written before the richer format existed."""
    def parse_time(value: Optional[str]) -> Optional[time]:
        return time.fromisoformat(value) if value else None

    kwargs: dict[str, Any] = {name: snapshot_item.get(name) for name in _ITEM_FIELDS}
    kwargs["sort_order"] = snapshot_item.get("sort_order") or 0
    kwargs["provider"] = snapshot_item.get("provider") or "restored"
    kwargs.update(
        trip_day_id=trip_day_id,
        item_type=TripItemType(snapshot_item["item_type"]),
        start_time=parse_time(snapshot_item.get("start_time")),
        end_time=parse_time(snapshot_item.get("end_time")),
    )
    return kwargs
