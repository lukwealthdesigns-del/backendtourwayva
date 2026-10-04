"""Pure validation/repair rules for generated itineraries."""
from datetime import date, time

import pytest

from app.modules.planning.domain import PlannedDay, PlannedItem, PlannedTrip, TripSpec
from app.modules.planning.rules import (
    haversine_km,
    needs_model_repair,
    plan_total_cost,
    repair_plan,
    validate_plan,
)

SPEC = TripSpec(destination="Paris", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3),
                travelers=2, budget_amount=1000.0, budget_currency="EUR")
CENTER = (48.8566, 2.3522)


def item(title, *, start=None, end=None, cost=None, lat=None, lon=None, kind="activity", **kw):
    return PlannedItem(item_type=kind, title=title, start_time=start, end_time=end, estimated_cost=cost,
                       currency="EUR" if cost else None, latitude=lat, longitude=lon, **kw)


def plan(*days_items, currency="EUR"):
    days = [PlannedDay(day_number=i + 1, date=date(2026, 10, 1 + i), items=list(items))
            for i, items in enumerate(days_items)]
    return PlannedTrip(overview="x", currency=currency, days=days)


def codes(issues, severity=None):
    return sorted(i.code for i in issues if severity is None or i.severity == severity)


def test_haversine_paris_to_versailles_is_about_17km():
    assert 15 < haversine_km(48.8566, 2.3522, 48.8049, 2.1204) < 19


def test_clean_plan_has_no_errors():
    p = plan(
        [item("Hotel", kind="hotel", cost=300), item("Louvre", start=time(9), end=time(12), cost=40, lat=48.8606, lon=2.3376)],
        [item("Orsay", start=time(10), end=time(12), cost=30, lat=48.86, lon=2.3266)],
        [item("Walk", start=time(10), end=time(11))],
    )
    assert codes(validate_plan(p, SPEC, destination_center=CENTER), "error") == []


def test_wrong_number_of_days_is_an_error():
    assert "date_mismatch" in codes(validate_plan(plan([item("a")], [item("b")]), SPEC), "error")


def test_time_conflict_and_duplicate_and_end_before_start():
    p = plan(
        [item("A", start=time(9), end=time(11)), item("B", start=time(10), end=time(12)),
         item("A", start=time(15), end=time(16)), item("C", start=time(17), end=time(16))],
        [item("x")], [item("y")],
    )
    found = codes(validate_plan(p, SPEC), "error")
    assert {"time_conflict", "duplicate_item", "end_before_start"} <= set(found)


def test_invalid_and_outlier_coordinates():
    p = plan([item("Half", lat=48.0), item("Mars", lat=95.0, lon=2.0), item("Lyon", lat=45.76, lon=4.83)],
             [item("x")], [item("y")])
    found = validate_plan(p, SPEC, destination_center=CENTER)
    assert codes(found).count("invalid_coordinates") == 2
    assert "outlier_location" in codes(found)


def test_impossible_travel_between_far_apart_stops():
    p = plan(
        [item("Louvre", start=time(9), end=time(10), lat=48.8606, lon=2.3376),
         item("Versailles", start=time(10, 5), end=time(12), lat=48.8049, lon=2.1204)],
        [item("x")], [item("y")],
    )
    assert "impossible_travel" in codes(validate_plan(p, SPEC), "error")


def test_over_budget_and_currency_mismatch():
    p = plan([item("Yacht", cost=1500)], [item("x")], [item("y")])
    assert "over_budget" in codes(validate_plan(p, SPEC), "error")
    p2 = plan([PlannedItem("activity", "Tour", estimated_cost=50, currency="USD")], [item("x")], [item("y")])
    assert "currency_mismatch" in codes(validate_plan(p2, SPEC), "error")


def test_weather_conflict_only_where_a_forecast_exists():
    p = plan([item("Picnic", outdoor=True)], [item("Hike", outdoor=True)], [item("Boat", outdoor=True)])
    forecast = [{"date": "2026-10-01", "condition": "Heavy rain", "chance_of_rain_pct": 90},
                {"date": "2026-10-02", "condition": "Sunny", "chance_of_rain_pct": 5}]  # day 3: no forecast
    weather = [i for i in validate_plan(p, SPEC, forecast=forecast) if i.code == "weather_conflict"]
    assert [(i.day_number, i.severity) for i in weather] == [(1, "warning")]      # not day 3: no claim without data


def test_missing_accommodation_and_repeated_item_are_warnings_only():
    p = plan([item("Louvre")], [item("Louvre")], [item("y")])
    found = validate_plan(p, SPEC)
    assert {"no_accommodation", "repeated_item"} <= set(codes(found, "warning"))
    assert "no_accommodation" not in codes(found, "error")


def test_repair_removes_duplicates_and_fixes_times():
    p = plan(
        [item("A", start=time(9), end=time(11)), item("B", start=time(10), end=time(12)),
         item("A", start=time(15), end=time(16))],
        [item("x")], [item("y")],
    )
    changes = repair_plan(p, SPEC, validate_plan(p, SPEC))
    assert changes and "duplicate" in " ".join(changes).lower()
    assert codes(validate_plan(p, SPEC), "error") == []
    starts = [i.start_time for i in p.days[0].items if i.start_time]
    assert starts == sorted(starts)


def test_repair_shifts_a_stop_to_allow_travel_time():
    p = plan(
        [item("Louvre", start=time(9), end=time(10), lat=48.8606, lon=2.3376),
         item("Versailles", start=time(10, 5), end=time(12), lat=48.8049, lon=2.1204)],
        [item("x")], [item("y")],
    )
    repair_plan(p, SPEC, validate_plan(p, SPEC))
    assert codes(validate_plan(p, SPEC), "error") == []
    assert p.days[0].items[1].start_time > time(10, 5)


def test_repair_makes_item_flexible_when_the_day_has_no_room():
    p = plan([item("Late show", start=time(23, 0), end=time(23, 50)), item("Later", start=time(23, 30), end=time(23, 59))],
             [item("x")], [item("y")])
    repair_plan(p, SPEC, validate_plan(p, SPEC))
    assert codes(validate_plan(p, SPEC), "error") == []
    assert any(i.start_time is None for i in p.days[0].items)


def test_budget_trim_drops_expensive_optional_items_but_keeps_hotel_and_must_see():
    p = plan(
        [item("Hotel", kind="hotel", cost=600), item("Show", cost=300), item("Museum", cost=60, must_see=True)],
        [item("Tour", cost=250)], [item("y")],
    )
    assert plan_total_cost(p) == 1210
    repair_plan(p, SPEC, validate_plan(p, SPEC))
    titles = [i.title for d in p.days for i in d.items]
    assert "Hotel" in titles and "Museum" in titles          # never dropped
    assert plan_total_cost(p) <= 1000
    assert "Show" not in titles                              # most expensive optional went first


def test_unfixable_budget_stays_an_error_so_it_is_never_saved():
    p = plan([item("Hotel", kind="hotel", cost=2000)], [item("x")], [item("y")])
    repair_plan(p, SPEC, validate_plan(p, SPEC))
    assert "over_budget" in codes(validate_plan(p, SPEC), "error")


def test_needs_model_repair_decisions():
    clean = validate_plan(plan([item("a")], [item("b")], [item("c")]), SPEC)
    assert not needs_model_repair(clean, 0)
    bad = validate_plan(plan([item("a")], [item("b")]), SPEC)
    assert needs_model_repair(bad, 2)                         # errors always trigger
    wet_plan = plan([item("Picnic", outdoor=True)], [item("b")], [item("c")])
    wet = validate_plan(wet_plan, SPEC, forecast=[{"date": "2026-10-01", "condition": "Rain", "chance_of_rain_pct": 95}])
    assert needs_model_repair(wet, 0) and not needs_model_repair(wet, 1)   # weather: one try, never a loop


def test_a_day_with_nothing_planned_is_an_error():
    found = validate_plan(plan([item("a")], [], [item("c")]), SPEC)
    assert [(i.code, i.day_number) for i in found if i.code == "empty_day"] == [("empty_day", 2)]


def test_budget_trim_never_empties_a_day():
    p = plan([item("Hotel", kind="hotel", cost=100), item("Show", cost=400)], [item("Gala", cost=900)], [item("y")])
    repair_plan(p, SPEC, validate_plan(p, SPEC))
    assert all(d.items for d in p.days)                          # the lone item of day 2 stays
    assert "Show" not in [i.title for d in p.days for i in d.items]
