"""Version snapshots must be COMPLETE and restorable (Master Prompt §19)."""
import uuid
from datetime import date, time
from types import SimpleNamespace as NS

from app.core.constants import TripItemType
from app.modules.itinerary.snapshots import build_snapshot, item_to_snapshot, snapshot_item_kwargs


def _item(**over):
    base = dict(id=uuid.uuid4(), item_type=TripItemType.ACTIVITY, title="Seine Cruise", description="1h cruise",
                location_name="Seine, Paris", latitude=48.85, longitude=2.29, start_time=time(9, 30),
                end_time=time(11, 0), estimated_cost=40.0, currency="EUR", provider="amadeus", source="provider",
                external_id="AX1", booking_link="https://book/x", image_url="https://img/x.jpg", notes="bring a coat",
                sort_order=2)
    base.update(over)
    return NS(**base)


def test_snapshot_round_trip_preserves_every_field():
    day = NS(day_number=1, date=date(2026, 10, 1), weather_summary="Sunny, 18-22°C")
    snap = build_snapshot("A lovely trip", [(day, [_item()])])

    assert snap["overview"] == "A lovely trip" and snap["days"][0]["weather_summary"] == "Sunny, 18-22°C"
    restored = snapshot_item_kwargs(snap["days"][0]["items"][0], uuid.uuid4())
    for field in ("title", "description", "location_name", "latitude", "longitude", "estimated_cost", "currency",
                  "provider", "source", "external_id", "booking_link", "image_url", "notes", "sort_order"):
        assert restored[field] == getattr(_item(), field) or field == "id", field
    assert restored["item_type"] is TripItemType.ACTIVITY
    assert (restored["start_time"], restored["end_time"]) == (time(9, 30), time(11, 0))


def test_untimed_items_and_missing_optional_fields_round_trip():
    snap = item_to_snapshot(_item(start_time=None, end_time=None, description=None, source=None))
    restored = snapshot_item_kwargs(snap, uuid.uuid4())
    assert restored["start_time"] is None and restored["end_time"] is None and restored["description"] is None


def test_old_style_snapshots_still_restore():
    """Snapshots written before the richer format only had a handful of fields."""
    old = {"item_type": "hotel", "title": "Hotel", "start_time": None, "end_time": None, "sort_order": 0}
    restored = snapshot_item_kwargs(old, uuid.uuid4())
    assert restored["item_type"] is TripItemType.HOTEL and restored["provider"] == "restored"
    assert restored["description"] is None and restored["external_id"] is None
