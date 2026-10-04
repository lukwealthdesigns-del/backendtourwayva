"""
PendingChangeService — the concrete implementation of "AI proposes,
systems verify, users decide" (Master Blueprint Principle 2, and §18
conversational itinerary modification) for Companion.

Flow:
  1. Companion calls a `propose_*` tool -> this service creates a
     PendingItineraryChange row (status=pending). NOTHING on the trip
     has changed yet.
  2. The person reviews the proposal (e.g. in the chat UI) and calls
     POST /companion/changes/{id}/confirm or /reject.
  3. On confirm, this service re-verifies the actor has editor/owner
     access to the trip (never trusts that the conversation's owner
     still has access, or that the original proposer does either —
     re-checked fresh), then applies the change through the exact
     same ItineraryService methods (and therefore the exact same
     ItineraryValidationService checks) a manual UI edit would use.
     On reject, nothing is applied; the row is just marked rejected.

This is intentionally the ONLY path from Companion to a trip mutation
— see app/modules/companion/tools.py, which has no direct
add/update/delete tools, only propose_* ones.
"""
from __future__ import annotations

import uuid
from datetime import time as time_cls

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PendingChangeAction, PendingChangeStatus, TripItemType
from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.db.models.pending_change import PendingItineraryChange
from app.modules.itinerary.schemas import TripItemCreateRequest, TripItemUpdateRequest
from app.modules.itinerary.service import ItineraryService
from app.modules.planning.llm_io import PlanParseError, plan_from_dict
from app.modules.planning.persistence import PlanningPersistence
from app.modules.trips.service import TripService
from app.repositories.pending_change_repository import PendingChangeRepository
from app.repositories.trip_repository import TripRepository


class PendingChangeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PendingChangeRepository(db)
        self.trip_repo = TripRepository(db)
        self.trip_service = TripService(db)
        self.itinerary_service = ItineraryService(db)

    # --- Proposal creation (called from companion/tools.py) ---

    async def propose_add_item(
        self, *, conversation_id: uuid.UUID, trip_id: uuid.UUID, proposer_id: uuid.UUID,
        day_number: int, item_fields: dict,
    ) -> PendingItineraryChange:
        # Any member (not just editor) may ask the AI to propose a
        # change — confirmation is where the editor gate applies.
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=proposer_id)

        day = await self.trip_repo.get_day_by_number(trip_id, day_number)
        if day is None:
            raise NotFoundError(f"Day {day_number} not found on this trip.")

        summary = f"Add '{item_fields.get('title', 'an item')}' to Day {day_number}"
        change = PendingItineraryChange(
            conversation_id=conversation_id, trip_id=trip_id, action=PendingChangeAction.ADD_ITEM,
            day_id=day.id, item_id=None, payload=item_fields, summary=summary,
            status=PendingChangeStatus.PENDING, proposed_by=proposer_id,
        )
        await self.repo.create(change)
        await self.db.commit()
        return change

    async def propose_update_item(
        self, *, conversation_id: uuid.UUID, item_id: uuid.UUID, proposer_id: uuid.UUID, item_fields: dict,
    ) -> PendingItineraryChange:
        item = await self.itinerary_service.repo.get_item(item_id)
        if item is None:
            raise NotFoundError("Trip item not found.")
        day = await self.trip_repo.get_day(item.trip_day_id)
        await self.trip_service.get_trip_authorized(trip_id=day.trip_id, user_id=proposer_id)

        summary = f"Update '{item.title}': " + ", ".join(f"{k}={v}" for k, v in item_fields.items())
        change = PendingItineraryChange(
            conversation_id=conversation_id, trip_id=day.trip_id, action=PendingChangeAction.UPDATE_ITEM,
            day_id=day.id, item_id=item.id, payload=item_fields, summary=summary[:500],
            status=PendingChangeStatus.PENDING, proposed_by=proposer_id,
        )
        await self.repo.create(change)
        await self.db.commit()
        return change

    async def propose_delete_item(
        self, *, conversation_id: uuid.UUID, item_id: uuid.UUID, proposer_id: uuid.UUID,
    ) -> PendingItineraryChange:
        item = await self.itinerary_service.repo.get_item(item_id)
        if item is None:
            raise NotFoundError("Trip item not found.")
        day = await self.trip_repo.get_day(item.trip_day_id)
        await self.trip_service.get_trip_authorized(trip_id=day.trip_id, user_id=proposer_id)

        summary = f"Remove '{item.title}' from Day {day.day_number}"
        change = PendingItineraryChange(
            conversation_id=conversation_id, trip_id=day.trip_id, action=PendingChangeAction.DELETE_ITEM,
            day_id=day.id, item_id=item.id, payload={}, summary=summary,
            status=PendingChangeStatus.PENDING, proposed_by=proposer_id,
        )
        await self.repo.create(change)
        await self.db.commit()
        return change

    # --- Confirm / reject (called from the HTTP API, by a human) ---

    async def get_change(self, change_id: uuid.UUID) -> PendingItineraryChange:
        change = await self.repo.get_by_id(change_id)
        if change is None:
            raise NotFoundError("Proposed change not found.")
        return change

    async def confirm(self, *, change_id: uuid.UUID, actor_id: uuid.UUID):
        change = await self.get_change(change_id)
        if change.status != PendingChangeStatus.PENDING:
            raise ValidationAppError(f"This change was already {change.status.value}.")

        # Fresh, independent authorization check — never trusts that
        # the proposer (or anyone) still has access.
        await self.trip_service.get_trip_authorized(trip_id=change.trip_id, user_id=actor_id, require_editor=True)

        if change.action == PendingChangeAction.ADD_ITEM:
            day = await self.trip_repo.get_day(change.day_id)
            payload = TripItemCreateRequest(**self._deserialize_item_fields(change.payload))
            item, validation = await self.itinerary_service.add_item(day=day, payload=payload, actor_id=actor_id)
            result = {"item_id": str(item.id), "validation": validation.model_dump()}

        elif change.action == PendingChangeAction.UPDATE_ITEM:
            item = await self.itinerary_service.repo.get_item(change.item_id)
            if item is None:
                raise NotFoundError("The item this change targets no longer exists.")
            payload = TripItemUpdateRequest(**self._deserialize_item_fields(change.payload))
            updated_item, validation = await self.itinerary_service.update_item(
                item=item, payload=payload, actor_id=actor_id
            )
            result = {"item_id": str(updated_item.id), "validation": validation.model_dump()}

        elif change.action == PendingChangeAction.DELETE_ITEM:
            item = await self.itinerary_service.repo.get_item(change.item_id)
            if item is None:
                raise NotFoundError("The item this change targets no longer exists.")
            await self.itinerary_service.delete_item(item=item, actor_id=actor_id)
            result = {"deleted": True}

        elif change.action == PendingChangeAction.REVISE_TRIP:
            result = await self._apply_revision(change, actor_id)

        else:  # pragma: no cover — exhaustive over the enum
            raise ValidationAppError(f"Unknown change action '{change.action}'.")

        change.status = PendingChangeStatus.CONFIRMED
        change.decided_by = actor_id
        await self.repo.save(change)
        await self.db.commit()

        await self._notify_decision(change, decision="confirmed")
        return change, result

    async def _apply_revision(self, change: PendingItineraryChange, actor_id: uuid.UUID) -> dict:
        """Apply a stored, already-validated revision as ONE transaction with a
        new version snapshot — but only if the trip has not changed since the
        proposal was made (otherwise it would silently overwrite newer edits)."""
        trip = await self.trip_repo.get_trip(change.trip_id)
        if trip is None:
            raise NotFoundError("Trip not found.")
        payload = change.payload or {}
        if trip.current_version_number != payload.get("base_version"):
            raise ConflictError(
                "The itinerary changed after this revision was proposed. Ask again to get an up-to-date proposal."
            )
        try:
            plan = plan_from_dict(payload["plan"])
        except (PlanParseError, KeyError) as exc:
            raise ValidationAppError("This proposal is malformed and cannot be applied.") from exc

        outcome = await PlanningPersistence(self.db, trip_id=change.trip_id, actor_id=actor_id, commit=False)(
            plan, {"change_summary": change.summary}
        )
        return {"version_number": outcome["version_number"], "items": outcome["items"], "applied": True}

    async def reject(self, *, change_id: uuid.UUID, actor_id: uuid.UUID) -> PendingItineraryChange:
        change = await self.get_change(change_id)
        if change.status != PendingChangeStatus.PENDING:
            raise ValidationAppError(f"This change was already {change.status.value}.")

        await self.trip_service.get_trip_authorized(trip_id=change.trip_id, user_id=actor_id, require_editor=True)

        change.status = PendingChangeStatus.REJECTED
        change.decided_by = actor_id
        await self.repo.save(change)
        await self.db.commit()

        await self._notify_decision(change, decision="rejected")
        return change

    async def list_for_conversation(self, conversation_id: uuid.UUID):
        return await self.repo.list_for_conversation(conversation_id)

    @staticmethod
    def _deserialize_item_fields(payload: dict) -> dict:
        """Payload is stored as plain JSON (strings for time/enum
        fields); convert back to the types the itinerary schemas
        expect before constructing them."""
        data = dict(payload)
        if data.get("start_time"):
            data["start_time"] = time_cls.fromisoformat(data["start_time"])
        if data.get("end_time"):
            data["end_time"] = time_cls.fromisoformat(data["end_time"])
        if data.get("item_type"):
            data["item_type"] = TripItemType(data["item_type"])
        return data

    async def _notify_decision(self, change: PendingItineraryChange, *, decision: str) -> None:
        """Best-effort notification to whoever proposed the change
        that a human has confirmed or rejected it — never blocks the
        confirm/reject flow itself if notification delivery fails."""
        try:
            from app.core.constants import NotificationType
            from app.modules.notifications.service import NotificationService

            await NotificationService(self.db).notify(
                user_id=change.proposed_by,
                notification_type=NotificationType.ITINERARY_CHANGE_DECIDED,
                title=f"Your proposed change was {decision}",
                body=change.summary,
                link=str(change.trip_id),
            )
        except Exception:  # noqa: BLE001
            pass
