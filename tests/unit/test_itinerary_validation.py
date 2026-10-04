import uuid
from datetime import date, time

from app.core.constants import TripItemType
from app.db.models.trip import TripDay, TripItem
from app.modules.itinerary.validation_service import ItineraryValidationService

validator = ItineraryValidationService()


def _make_item(**kwargs) -> TripItem:
    defaults = dict(
        id=uuid.uuid4(),
        trip_day_id=uuid.uuid4(),
        item_type=TripItemType.ACTIVITY,
        title="Museum Visit",
        location_name="Louvre",
        latitude=48.8606,
        longitude=2.3376,
        start_time=None,
        end_time=None,
        sort_order=0,
    )
    defaults.update(kwargs)
    return TripItem(**defaults)


def _make_day() -> TripDay:
    return TripDay(id=uuid.uuid4(), trip_id=uuid.uuid4(), day_number=1, date=date(2026, 10, 1))


def test_no_issues_for_clean_day():
    day = _make_day()
    items = [
        _make_item(title="Breakfast", start_time=time(8, 0), end_time=time(9, 0)),
        _make_item(title="Louvre Tour", start_time=time(10, 0), end_time=time(12, 0)),
    ]
    result = validator.validate_day(day, items)
    assert result.is_valid
    assert result.issues == []


def test_detects_time_conflict():
    day = _make_day()
    items = [
        _make_item(title="Louvre Tour", start_time=time(10, 0), end_time=time(12, 0)),
        _make_item(title="Eiffel Tower", start_time=time(11, 0), end_time=time(13, 0)),
    ]
    result = validator.validate_day(day, items)
    assert not result.is_valid
    assert any(i.code == "time_conflict" for i in result.issues)


def test_detects_duplicate_activity():
    day = _make_day()
    items = [
        _make_item(title="Louvre Tour", location_name="Louvre"),
        _make_item(title="Louvre Tour", location_name="Louvre"),
    ]
    result = validator.validate_day(day, items)
    assert not result.is_valid
    assert any(i.code == "duplicate_activity" for i in result.issues)


def test_detects_invalid_coordinates():
    day = _make_day()
    items = [_make_item(title="Somewhere", latitude=200.0, longitude=2.33)]
    result = validator.validate_day(day, items)
    assert not result.is_valid
    assert any(i.code == "invalid_coordinates" for i in result.issues)


def test_detects_excessive_daily_activity():
    day = _make_day()
    items = [
        _make_item(title=f"Activity {n}", start_time=time(8 + n, 0), end_time=time(8 + n, 30))
        for n in range(9)
    ]
    result = validator.validate_day(day, items)
    assert not result.is_valid
    assert any(i.code == "excessive_daily_activity" for i in result.issues)


def test_adjacent_non_overlapping_times_are_fine():
    day = _make_day()
    items = [
        _make_item(title="Breakfast", start_time=time(8, 0), end_time=time(9, 0)),
        _make_item(title="Walk", start_time=time(9, 0), end_time=time(10, 0)),
    ]
    result = validator.validate_day(day, items)
    assert result.is_valid
