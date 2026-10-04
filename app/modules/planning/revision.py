"""
Conversational itinerary revision (Master Prompt §18): "make this trip
cheaper", "remove Day 3", "reduce walking", "change my hotel"...

Same discipline as generation — the model proposes, the system verifies, the
user decides:

    draft_revision ─► ground_items ─► validate ─┬─► persist (= store a PENDING proposal)
                          ▲                      ├─► repair ─┐
                          └──────────────────────┘           │
                                                             └─► fail

Nothing here writes to the itinerary. The result is a PendingItineraryChange
that a human must confirm; confirming applies it as one transaction with a new
version snapshot. The workflow REUSES the generation nodes (ground / validate /
repair), so a revision is held to exactly the same rules as a fresh plan.

Pure helpers first (plan_from_db, catalog_from_plan, diff_plans) — no framework
imports — then the graph.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

from app.core.exceptions import ProviderUnavailableError
from app.modules.planning.domain import PlannedDay, PlannedItem, PlannedTrip
from app.modules.planning.graph import (
    PlanningPorts,
    _fail,
    make_nodes,
    route_after_draft,
    route_after_validate,
)
from app.modules.planning.llm_io import PlanParseError, build_revision_messages, parse_llm_plan
from app.modules.planning.rules import plan_total_cost
from app.modules.planning.workflow import WorkflowSpec

logger = logging.getLogger(__name__)

MAX_INSTRUCTION_CHARS = 500


# ---------------------------------------------------------------------------
# ORM -> PlannedTrip (duck-typed)
# ---------------------------------------------------------------------------
def plan_from_db(trip: Any, days_with_items: Iterable[tuple[Any, list[Any]]], *, default_currency: str = "USD") -> PlannedTrip:
    days: list[PlannedDay] = []
    currency: Optional[str] = (getattr(trip, "budget_currency", None) or None)
    for day, items in days_with_items:
        planned_items: list[PlannedItem] = []
        for item in items:
            currency = currency or item.currency
            planned_items.append(PlannedItem(
                item_type=item.item_type.value if hasattr(item.item_type, "value") else str(item.item_type),
                title=item.title,
                description=item.description,
                location_name=item.location_name,
                latitude=item.latitude,
                longitude=item.longitude,
                start_time=item.start_time,
                end_time=item.end_time,
                estimated_cost=item.estimated_cost,
                currency=item.currency,
                provider=item.provider if item.provider not in ("manual", "restored", "ai") else None,
                source="provider" if item.source == "provider" else "estimated",
                external_id=item.external_id if item.source == "provider" else None,
                booking_link=item.booking_link,
                image_url=item.image_url,
                notes=item.notes,
            ))
        days.append(PlannedDay(
            day_number=day.day_number, date=day.date, items=planned_items,
            weather_summary=getattr(day, "weather_summary", None),
        ))
    return PlannedTrip(overview=getattr(trip, "overview", None) or "", currency=(currency or default_currency).upper(), days=days)


def catalog_from_plan(plan: PlannedTrip) -> dict[str, dict[str, Any]]:
    """The verified provider items already in the itinerary, in the shape the
    grounding step expects — so a revision keeps them (with their stored,
    provider-verified name/price/coordinates) instead of downgrading them."""
    catalog: dict[str, dict[str, Any]] = {}
    for day in plan.days:
        for item in day.items:
            if item.source != "provider" or not item.external_id:
                continue
            common = {
                "description": item.description, "group_cost": item.estimated_cost, "provider": item.provider,
                "latitude": item.latitude, "longitude": item.longitude,
            }
            if item.item_type == "hotel":
                catalog[item.external_id] = {"kind": "hotel", "hotel_name": item.title,
                                             "room_description": item.description, **common}
            else:
                catalog[item.external_id] = {"kind": "activity", "name": item.title, "booking_link": item.booking_link,
                                             "picture_url": item.image_url, **common}
    return catalog


# ---------------------------------------------------------------------------
# Diff + human-readable summary
# ---------------------------------------------------------------------------
@dataclass
class PlanDiff:
    added: list[tuple[int, str]] = field(default_factory=list)          # (day, title)
    removed: list[tuple[int, str]] = field(default_factory=list)
    moved: list[tuple[str, int, int]] = field(default_factory=list)     # (title, from_day, to_day)
    retimed: list[tuple[int, str]] = field(default_factory=list)
    cost_before: float = 0.0
    cost_after: float = 0.0
    currency: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.moved or self.retimed) and self.cost_before == self.cost_after


def _key(item: PlannedItem) -> str:
    return item.external_id or f"{item.title.strip().lower()}|{(item.location_name or '').strip().lower()}"


def _index(plan: PlannedTrip) -> dict[str, list[tuple[int, PlannedItem]]]:
    index: dict[str, list[tuple[int, PlannedItem]]] = {}
    for day in plan.days:
        for item in day.items:
            index.setdefault(_key(item), []).append((day.day_number, item))
    return index


def diff_plans(before: PlannedTrip, after: PlannedTrip) -> PlanDiff:
    old, new = _index(before), _index(after)
    diff = PlanDiff(cost_before=plan_total_cost(before), cost_after=plan_total_cost(after), currency=after.currency)
    for key, entries in new.items():
        previous = old.get(key, [])
        for position, (day, item) in enumerate(entries):
            if position >= len(previous):
                diff.added.append((day, item.title))
                continue
            old_day, old_item = previous[position]
            if old_day != day:
                diff.moved.append((item.title, old_day, day))
            elif (old_item.start_time, old_item.end_time) != (item.start_time, item.end_time):
                diff.retimed.append((day, item.title))
    for key, entries in old.items():
        for position, (day, item) in enumerate(entries):
            if position >= len(new.get(key, [])):
                diff.removed.append((day, item.title))
    return diff


def _names(entries: list, limit: int = 3) -> str:
    titles = [e[1] if isinstance(e, tuple) and len(e) == 2 else e[0] for e in entries]
    shown = ", ".join(f"'{t}'" for t in titles[:limit])
    return shown + (f" and {len(titles) - limit} more" if len(titles) > limit else "")


def summarize_diff(diff: PlanDiff, instruction: str, *, limit: int = 480) -> str:
    parts = []
    if diff.removed:
        parts.append(f"removes {len(diff.removed)} item(s) ({_names(diff.removed)})")
    if diff.added:
        parts.append(f"adds {len(diff.added)} ({_names(diff.added)})")
    if diff.moved:
        parts.append(f"moves {len(diff.moved)} ({_names(diff.moved)})")
    if diff.retimed:
        parts.append(f"changes the time of {len(diff.retimed)}")
    if diff.cost_before != diff.cost_after:
        parts.append(f"estimated cost {diff.cost_before:,.2f} → {diff.cost_after:,.2f} {diff.currency}")
    text = f"Revise trip: \"{instruction}\" — " + ("; ".join(parts) or "no visible change")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------
def revision_ports(
    *,
    geocode: Callable[..., Awaitable[Any]],
    llm: Callable[..., Awaitable[str]],
    store: Callable[..., Awaitable[dict[str, Any]]],
    hours_from: Optional[PlanningPorts] = None,
) -> PlanningPorts:
    """Ports for a revision: no provider searches (a revision reshapes what is
    already planned; for bookable options the Companion uses its search tools).
    `hours_from` (the full generation ports) opts the revision into the same
    opening-hours check, so a requested change cannot quietly move a visit to a
    time the venue is closed."""

    async def unused(*args: Any, **kwargs: Any):
        raise ProviderUnavailableError("Not used by itinerary revisions.")

    ports = PlanningPorts(
        geocode=geocode, search_hotels=unused, search_activities=unused, forecast=unused,
        convert_rate=unused, llm=llm, persist=store,
    )
    if hours_from is not None:
        ports.opening_hours = hours_from.opening_hours
        ports.opening_hours_enabled = hours_from.opening_hours_enabled
        ports.max_opening_hours_lookups = hours_from.max_opening_hours_lookups
        ports.opening_hours_timeout_seconds = hours_from.opening_hours_timeout_seconds
    return ports


def build_revision_spec(ports: PlanningPorts) -> WorkflowSpec:
    base = make_nodes(ports)

    async def draft_revision(state: dict[str, Any]) -> dict[str, Any]:
        messages = build_revision_messages(
            spec=state["spec"], currency=state["currency"], plan=state["base_plan"],
            instruction=state["instruction"], language=state.get("language", "en"),
        )
        for temperature in (0.3, 0.1):
            try:
                raw = await ports.llm(messages, temperature, 4000)
            except ProviderUnavailableError:
                return _fail("ai_unavailable", "The AI planner is temporarily unavailable. Please try again.", retryable=True)
            try:
                return {"plan": parse_llm_plan(raw, state["spec"], state["currency"])}
            except PlanParseError as exc:
                logger.warning("planning_revision_output_rejected: %s", exc)
        return _fail("ai_output_invalid", "The AI planner returned an unusable revision. Please try again.", retryable=True)

    spec = WorkflowSpec(
        entry="draft_revision",
        nodes={
            "draft_revision": draft_revision, "ground_items": base["ground_items"], "validate": base["validate"],
            "repair": base["repair"], "persist": base["persist"], "fail": base["fail"],
        },
        edges=[("ground_items", "validate"), ("repair", "ground_items")],
        conditionals={
            "draft_revision": (route_after_draft, {"ground_items": "ground_items", "fail": "fail"}),
            "validate": (route_after_validate, {"persist": "persist", "repair": "repair", "fail": "fail"}),
        },
        finish=["persist", "fail"],
    )
    spec.validate()
    return spec
