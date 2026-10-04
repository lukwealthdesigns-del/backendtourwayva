"""
RevisionService — turns "make it cheaper" / "remove day 3" into a PENDING,
validated proposal (Master Prompt §18). Never edits the itinerary itself.

  1. verify the caller belongs to the trip (any member may ASK; a human editor
     must CONFIRM — see PendingChangeService)
  2. load the current itinerary as a plan, run the revision workflow
     (model draft → grounding → validation → repair)
  3. diff against the current plan; if nothing would change, say so
  4. otherwise store a PendingItineraryChange(REVISE_TRIP) holding the whole
     validated plan, the diff summary and the trip's version at proposal time
     (so a stale proposal can be detected at confirmation)
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FeatureFlag, PendingChangeAction, PendingChangeStatus
from app.core.exceptions import ValidationAppError
from app.db.models.pending_change import PendingItineraryChange
from app.db.models.user import User
from app.modules.entitlements.service import EntitlementService
from app.modules.planning.deps import build_ports
from app.modules.planning.graph import PlanningState
from app.modules.planning.llm_io import plan_to_dict
from app.modules.planning.revision import (
    MAX_INSTRUCTION_CHARS,
    build_revision_spec,
    catalog_from_plan,
    diff_plans,
    plan_from_db,
    revision_ports,
    summarize_diff,
)
from app.modules.planning.service import PlanningService, Runner, _langgraph_runner
from app.modules.trips.service import TripService
from app.providers.llm.interface import LLMProvider
from app.repositories.pending_change_repository import PendingChangeRepository
from app.repositories.trip_repository import TripRepository


def clean_instruction(raw: Any) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        raise ValidationAppError("Describe the change you want to make to the itinerary.")
    return text[:MAX_INSTRUCTION_CHARS]


class RevisionService:
    def __init__(self, db: AsyncSession, *, llm: Optional[LLMProvider] = None, runner: Optional[Runner] = None):
        self.db = db
        self.llm = llm
        self._runner: Runner = runner or _langgraph_runner
        self.trip_repo = TripRepository(db)
        self.pending_repo = PendingChangeRepository(db)

    async def propose(
        self, *, conversation_id: uuid.UUID, trip_id: uuid.UUID, user: User, instruction: str
    ) -> dict[str, Any]:
        instruction = clean_instruction(instruction)
        trip = await TripService(self.db).get_trip_authorized(trip_id=trip_id, user_id=user.id)

        days = await self.trip_repo.list_days_for_trip(trip_id)
        days_with_items = [(d, list(await self.trip_repo.list_items_for_day(d.id))) for d in days]
        if not any(items for _, items in days_with_items):
            raise ValidationAppError("This trip has no itinerary yet. Generate one first, then ask for changes.")

        base = plan_from_db(trip, days_with_items, default_currency=user.currency or "USD")
        spec = PlanningService._spec_from_trip(trip)
        base_version = trip.current_version_number

        async def store(plan, meta) -> dict[str, Any]:
            diff = diff_plans(base, plan)
            if diff.is_empty:
                return {"changed": False, "message": "That request would not change the itinerary."}
            summary = summarize_diff(diff, instruction)
            change = await self.pending_repo.create(PendingItineraryChange(
                conversation_id=conversation_id, trip_id=trip_id, action=PendingChangeAction.REVISE_TRIP,
                day_id=None, item_id=None, summary=summary, status=PendingChangeStatus.PENDING, proposed_by=user.id,
                payload={
                    "instruction": instruction, "base_version": base_version, "plan": plan_to_dict(plan),
                    "warnings": meta.get("warnings", []), "repairs": meta.get("repairs", []),
                },
            ))
            await self.db.commit()
            return {
                "changed": True, "change_id": str(change.id), "summary": summary,
                "estimated_cost_before": diff.cost_before, "estimated_cost_after": diff.cost_after,
                "currency": diff.currency, "warnings": meta.get("warnings", []), "repairs": meta.get("repairs", []),
            }

        premium_ai = await EntitlementService(self.db).has_feature(user.id, FeatureFlag.PREMIUM_AI)
        full = build_ports(self.db, user_id=user.id, trip_id=trip_id, llm=self.llm, premium_ai=premium_ai)
        ports = revision_ports(geocode=full.geocode, llm=full.llm, store=store, hours_from=full)

        initial: dict[str, Any] = {
            "spec": spec, "currency": base.currency, "language": user.language or "en", "base_plan": base,
            "instruction": instruction, "catalog": catalog_from_plan(base), "allow_empty_days": True,
            "hotels_for_model": [], "activities_for_model": [], "forecast": [], "data_sources": {},
            "repair_attempts": 0, "repairs": [], "notes": [], "issues": [],
        }
        try:
            initial["geo"] = await full.geocode(trip.destination)   # anchors location verification to the destination
        except Exception:  # noqa: BLE001 - optional: verification still runs, just unanchored
            pass

        state = await self._runner(build_revision_spec(ports), initial)
        if state.get("error"):
            raise ValidationAppError(state["error"]["message"], details={"code": state["error"]["code"]})
        return state["result"]
