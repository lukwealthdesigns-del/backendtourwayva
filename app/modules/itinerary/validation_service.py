"""
ItineraryValidationService — Master Blueprint §16.

Implements the checks that are meaningful without an AI planner in
the loop yet (Phase 4 will add budget-aware repair and AI-driven
re-optimization on top of this):

  - Time conflicts: two timed items on the same day overlap
  - Duplicate activities: same title + location on the same day
  - Invalid coordinates: latitude/longitude out of range (defense in
    depth — Pydantic already rejects this at the schema layer, but
    the validation service re-checks stored data independently, since
    it may run against data written by a future AI pipeline that
    bypasses the HTTP schema layer entirely)
  - Excessive daily activity: more than a configurable number of
    timed items crammed into one day

This does NOT yet implement: budget violations (needs trip-level
cost aggregation across hotels/flights, deferred), geographic
inefficiency scoring (needs the Maps provider wired into a scoring
loop), or automatic repair (§16: "If validation fails -> repair
itinerary -> validate again" — repair requires AI, Phase 4).
"""
from __future__ import annotations

from collections import defaultdict

from app.db.models.trip import TripDay, TripItem
from app.modules.itinerary.schemas import ItineraryValidationResult, ValidationIssue

MAX_TIMED_ITEMS_PER_DAY = 8


class ItineraryValidationService:
    def validate_day(self, day: TripDay, items: list[TripItem]) -> ItineraryValidationResult:
        issues: list[ValidationIssue] = []

        issues.extend(self._check_time_conflicts(items))
        issues.extend(self._check_duplicates(items))
        issues.extend(self._check_invalid_coordinates(items))
        issues.extend(self._check_excessive_activity(items))

        return ItineraryValidationResult(is_valid=len(issues) == 0, issues=issues)

    @staticmethod
    def _check_time_conflicts(items: list[TripItem]) -> list[ValidationIssue]:
        timed = [i for i in items if i.start_time and i.end_time]
        timed.sort(key=lambda i: i.start_time)

        issues: list[ValidationIssue] = []
        for a, b in zip(timed, timed[1:]):
            if b.start_time < a.end_time:
                issues.append(
                    ValidationIssue(
                        code="time_conflict",
                        message=f"'{a.title}' ({a.start_time}-{a.end_time}) overlaps with "
                        f"'{b.title}' ({b.start_time}-{b.end_time}).",
                        item_ids=[a.id, b.id],
                    )
                )
        return issues

    @staticmethod
    def _check_duplicates(items: list[TripItem]) -> list[ValidationIssue]:
        seen: dict[tuple[str, str], list] = defaultdict(list)
        for item in items:
            key = (item.title.strip().lower(), (item.location_name or "").strip().lower())
            seen[key].append(item)

        issues: list[ValidationIssue] = []
        for (title, _location), matching_items in seen.items():
            if len(matching_items) > 1:
                issues.append(
                    ValidationIssue(
                        code="duplicate_activity",
                        message=f"'{title}' appears {len(matching_items)} times on this day.",
                        item_ids=[i.id for i in matching_items],
                    )
                )
        return issues

    @staticmethod
    def _check_invalid_coordinates(items: list[TripItem]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for item in items:
            lat, lon = item.latitude, item.longitude
            if lat is not None and not (-90 <= lat <= 90):
                issues.append(
                    ValidationIssue(
                        code="invalid_coordinates",
                        message=f"'{item.title}' has an invalid latitude ({lat}).",
                        item_ids=[item.id],
                    )
                )
            if lon is not None and not (-180 <= lon <= 180):
                issues.append(
                    ValidationIssue(
                        code="invalid_coordinates",
                        message=f"'{item.title}' has an invalid longitude ({lon}).",
                        item_ids=[item.id],
                    )
                )
        return issues

    @staticmethod
    def _check_excessive_activity(items: list[TripItem]) -> list[ValidationIssue]:
        timed_count = sum(1 for i in items if i.start_time)
        if timed_count > MAX_TIMED_ITEMS_PER_DAY:
            return [
                ValidationIssue(
                    code="excessive_daily_activity",
                    message=f"{timed_count} scheduled items on this day exceeds the recommended "
                    f"maximum of {MAX_TIMED_ITEMS_PER_DAY}.",
                    item_ids=[i.id for i in items if i.start_time],
                )
            ]
        return []
