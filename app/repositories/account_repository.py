"""
AccountRepository — the bulk queries behind user data export (Master
Prompt §76) and account deletion (§77). Kept in one place so the set of
tables that hold a user's personal data is explicit and reviewable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SubscriptionStatus, TripMemberRole
from app.db.models.admin import AdminMessage
from app.db.models.attachment import Attachment
from app.db.models.collaboration import PendingChangeVote, TripComment, TripInvitation
from app.db.models.conversation import Conversation, Message
from app.db.models.discover import DiscoveryResult, DiscoverySearch
from app.db.models.memory import UserMemory
from app.db.models.monetization import FeatureFlagOverride, Subscription, UserTrial
from app.db.models.notification import EmailLog, Notification, NotificationPreference
from app.db.models.payment import Payment
from app.db.models.otp import OTPCode
from app.db.models.preferences import UserPreferences
from app.db.models.security import LoginAttempt
from app.db.models.session import UserSession
from app.db.models.travel_history import VisitedDestination, VisitedPlace
from app.db.models.trip import Trip, TripCost, TripDay, TripItem, TripMember, TripNote
from app.db.models.user import User
from app.utils.serialization import row_to_dict

_REDACTED = "redacted"
# Roles in the order we prefer them when handing a trip to a new owner.
_OWNER_SUCCESSION = (TripMemberRole.EDITOR, TripMemberRole.CONTRIBUTOR, TripMemberRole.VIEWER)


class AccountRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _all(self, model, *conditions) -> list[Any]:
        result = await self.db.execute(select(model).where(*conditions))
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    async def export_bundle(self, user: User) -> dict[str, Any]:
        uid = user.id

        prefs = await self._all(UserPreferences, UserPreferences.user_id == uid)
        memories = await self._all(UserMemory, UserMemory.user_id == uid)

        member_rows = await self._all(TripMember, TripMember.user_id == uid)
        trip_ids = {m.trip_id for m in member_rows}
        owned = await self._all(Trip, Trip.owner_id == uid)
        trip_ids |= {t.id for t in owned}
        trips = await self._all(Trip, Trip.id.in_(trip_ids)) if trip_ids else []
        days = await self._all(TripDay, TripDay.trip_id.in_(trip_ids)) if trip_ids else []
        day_ids = [d.id for d in days]
        items = await self._all(TripItem, TripItem.trip_day_id.in_(day_ids)) if day_ids else []
        costs = await self._all(TripCost, TripCost.trip_id.in_(trip_ids)) if trip_ids else []
        notes = await self._all(TripNote, TripNote.user_id == uid)
        comments = await self._all(TripComment, TripComment.user_id == uid)

        conversations = await self._all(Conversation, Conversation.user_id == uid)
        conv_ids = [c.id for c in conversations]
        messages = await self._all(Message, Message.conversation_id.in_(conv_ids)) if conv_ids else []

        searches = await self._all(DiscoverySearch, DiscoverySearch.user_id == uid)
        search_ids = [s.id for s in searches]
        results = await self._all(DiscoveryResult, DiscoveryResult.search_id.in_(search_ids)) if search_ids else []

        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "profile": row_to_dict(user, exclude={"password_hash"}),
            "preferences": [row_to_dict(p) for p in prefs],
            "memories": [row_to_dict(m) for m in memories],
            "trips": [row_to_dict(t) for t in trips],
            "trip_memberships": [row_to_dict(m) for m in member_rows],
            "trip_days": [row_to_dict(d) for d in days],
            "trip_items": [row_to_dict(i) for i in items],
            "trip_costs": [row_to_dict(c) for c in costs],
            "trip_notes": [row_to_dict(n) for n in notes],
            "trip_comments": [row_to_dict(c) for c in comments],
            "conversations": [row_to_dict(c) for c in conversations],
            "messages": [row_to_dict(m) for m in messages],
            "attachments": [
                row_to_dict(a, exclude={"storage_key", "url"})
                for a in await self._all(Attachment, Attachment.user_id == uid)
            ],
            "visited_places": [row_to_dict(v) for v in await self._all(VisitedPlace, VisitedPlace.user_id == uid)],
            "visited_destinations": [
                row_to_dict(v) for v in await self._all(VisitedDestination, VisitedDestination.user_id == uid)
            ],
            "discovery_searches": [row_to_dict(s) for s in searches],
            "discovery_results": [row_to_dict(r) for r in results],
            "notifications": [row_to_dict(n) for n in await self._all(Notification, Notification.user_id == uid)],
            "notification_preferences": [
                row_to_dict(n) for n in await self._all(NotificationPreference, NotificationPreference.user_id == uid)
            ],
            "subscriptions": [
                row_to_dict(s, exclude={"paystack_email_token"})
                for s in await self._all(Subscription, Subscription.user_id == uid)
            ],
            "payments": [row_to_dict(p) for p in await self._all(Payment, Payment.user_id == uid)],
            "trials": [row_to_dict(t) for t in await self._all(UserTrial, UserTrial.user_id == uid)],
            "admin_messages_received": [
                row_to_dict(m) for m in await self._all(AdminMessage, AdminMessage.recipient_user_id == uid)
            ],
            "sessions": [
                row_to_dict(s, exclude={"refresh_jti_hash", "previous_jti_hash"})
                for s in await self._all(UserSession, UserSession.user_id == uid)
            ],
        }

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------
    async def resolve_owned_trips(self, user_id: uuid.UUID) -> tuple[dict[str, int], list[uuid.UUID]]:
        """Returns (summary counts, ids of deleted trips — the caller purges
        their stored PDFs). Trips the user owns: if other members exist, hand ownership to the
        most senior one (editor > contributor > viewer, then earliest
        joined) so collaborators do not lose the trip; otherwise delete the
        trip (children cascade in the database)."""
        transferred = deleted = 0
        deleted_trip_ids: list[uuid.UUID] = []
        for trip in await self._all(Trip, Trip.owner_id == user_id):
            others = await self._all(TripMember, TripMember.trip_id == trip.id, TripMember.user_id != user_id)
            successor = None
            for role in _OWNER_SUCCESSION:
                candidates = sorted((m for m in others if m.role == role), key=lambda m: m.joined_at)
                if candidates:
                    successor = candidates[0]
                    break
            if successor is not None:
                trip.owner_id = successor.user_id
                successor.role = TripMemberRole.OWNER
                await self.db.execute(
                    delete(TripMember).where(TripMember.trip_id == trip.id, TripMember.user_id == user_id)
                )
                transferred += 1
            else:
                await self.db.execute(delete(Trip).where(Trip.id == trip.id))
                deleted += 1
                deleted_trip_ids.append(trip.id)
        await self.db.flush()
        return {"trips_transferred": transferred, "trips_deleted": deleted}, deleted_trip_ids

    async def collect_attachment_storage_keys(self, user_id: uuid.UUID) -> list[str]:
        result = await self.db.execute(select(Attachment.storage_key).where(Attachment.user_id == user_id))
        return [row[0] for row in result.all()]

    async def delete_personal_data(self, user_id: uuid.UUID, email: str) -> None:
        """Hard-delete everything private to the user. (The users row itself
        is anonymized, not deleted — see AccountService — so shared records
        and legally required audit/security history keep valid references.)"""
        for model, column in (
            (TripMember, TripMember.user_id),
            (TripComment, TripComment.user_id),
            (PendingChangeVote, PendingChangeVote.user_id),
            (TripNote, TripNote.user_id),
            (UserMemory, UserMemory.user_id),
            (Conversation, Conversation.user_id),        # messages + pending changes cascade
            (Attachment, Attachment.user_id),
            (Notification, Notification.user_id),
            (NotificationPreference, NotificationPreference.user_id),
            (DiscoverySearch, DiscoverySearch.user_id),  # results cascade
            (VisitedPlace, VisitedPlace.user_id),
            (VisitedDestination, VisitedDestination.user_id),
            (UserTrial, UserTrial.user_id),
            (FeatureFlagOverride, FeatureFlagOverride.user_id),
            (OTPCode, OTPCode.user_id),
            (UserPreferences, UserPreferences.user_id),
            (UserSession, UserSession.user_id),
            (AdminMessage, AdminMessage.recipient_user_id),
        ):
            await self.db.execute(delete(model).where(column == user_id))

        # Invitations addressed to this email (or to this user by @username).
        await self.db.execute(
            delete(TripInvitation).where(
                or_(TripInvitation.recipient_email == email, TripInvitation.recipient_user_id == user_id)
            )
        )

        # Billing records (payments, subscription history) are KEPT for
        # accounting, but the plan stops renewing.
        await self.db.execute(
            update(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE)
            .values(status=SubscriptionStatus.CANCELLED)
        )

        # Records keyed by the raw email address: redact rather than delete.
        await self.db.execute(update(EmailLog).where(EmailLog.to_email == email).values(to_email=_REDACTED))
        await self.db.execute(update(LoginAttempt).where(LoginAttempt.email == email).values(email=_REDACTED))
        await self.db.flush()
