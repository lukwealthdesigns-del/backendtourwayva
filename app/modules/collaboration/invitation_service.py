"""
InvitationService (Master Blueprint §44).

Invitations carry: trip, inviter, recipient (email, or an existing
user's @username), role, a secure token, an expiration, and a status —
exactly the fields the blueprint specifies. An @username invite is
resolved to that user's account server-side: their email is stored (so
the acceptance gate below is unchanged) but never shown to the inviter. Acceptance is gated on the accepting user's own verified
email matching `recipient_email` (case-insensitive) — knowing the
token alone is not sufficient, which prevents a leaked/forwarded
invitation link from being accepted by the wrong account.
"""
from __future__ import annotations

from typing import Optional

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import InvitationStatus, UserStatus
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.db.models.collaboration import TripInvitation
from app.db.models.trip import TripMember
from app.db.models.user import User
from app.modules.collaboration.schemas import InviteMemberRequest
from app.modules.trips.service import TripService
from app.providers.email.factory import get_email_provider
from app.utils.html import esc
from app.providers.email.interface import EmailProvider
from app.repositories.collaboration_repository import CollaborationRepository
from app.repositories.user_repository import UserRepository

INVITATION_EXPIRY_DAYS = 7


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _invitation_email_html(*, inviter_name: str, trip_title: str, role: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;">
      <h2>Tour-Wayva</h2>
      <p>{esc(inviter_name)} invited you to collaborate on their trip "{esc(trip_title)}" as a {esc(role)}.</p>
      <p>Open the Tour-Wayva app and go to your invitations to accept or decline.</p>
      <p>This invitation expires in {INVITATION_EXPIRY_DAYS} days.</p>
      <p>— The Tour-Wayva Team</p>
    </div>
    """


class InvitationService:
    def __init__(self, db: AsyncSession, email_provider: Optional[EmailProvider] = None):
        self.db = db
        self.repo = CollaborationRepository(db)
        self.trip_service = TripService(db)
        self.email_provider = email_provider or get_email_provider(db)

    async def invite(
        self, *, trip_id: uuid.UUID, inviter: User, payload: InviteMemberRequest
    ) -> TripInvitation:
        trip = await self.trip_service.get_trip_authorized(
            trip_id=trip_id, user_id=inviter.id, require_editor=True
        )

        recipient_user, recipient_email = await self._resolve_recipient(payload)
        await self._check_invitable(
            trip_id=trip_id, inviter=inviter, recipient_user=recipient_user, recipient_email=recipient_email
        )

        invitation = TripInvitation(
            trip_id=trip_id,
            inviter_id=inviter.id,
            recipient_email=recipient_email,
            recipient_user_id=recipient_user.id if payload.recipient_username else None,
            role=payload.role,
            token=_generate_token(),
            status=InvitationStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(days=INVITATION_EXPIRY_DAYS),
        )
        await self.repo.create_invitation(invitation)
        await self.db.commit()

        try:
            await self.email_provider.send_transactional_email(
                to_email=invitation.recipient_email,
                subject=f"{inviter.first_name} invited you to a Tour-Wayva trip",
                html_content=_invitation_email_html(
                    inviter_name=inviter.first_name, trip_title=trip.title, role=payload.role.value
                ),
            )
        except Exception:  # noqa: BLE001
            # Never fail the invitation creation over an email hiccup
            # — the invitation record (and its token) still exists;
            # the recipient can still be told out-of-band if needed.
            pass

        # If the invited email already belongs to a Tour-Wayva
        # account, also raise an in-app notification — best-effort,
        # never blocks invitation creation.
        try:
            from app.modules.notifications.service import NotificationService
            from app.core.constants import NotificationType

            existing_recipient = recipient_user or await UserRepository(self.db).get_by_email(invitation.recipient_email)
            if existing_recipient is not None:
                await NotificationService(self.db).notify(
                    user_id=existing_recipient.id,
                    notification_type=NotificationType.TRIP_INVITATION,
                    title=f"{inviter.first_name} invited you to a trip",
                    body=f"You've been invited to collaborate on '{trip.title}' as {payload.role.value}.",
                    link=str(trip_id),
                )
        except Exception:  # noqa: BLE001
            pass

        return invitation

    async def _resolve_recipient(self, payload: InviteMemberRequest) -> tuple[Optional[User], str]:
        """Return (existing user or None, lowercase recipient email)."""
        users = UserRepository(self.db)
        if payload.recipient_username:
            user = await users.get_by_username(payload.recipient_username)
            if user is None or not user.is_active or user.status != UserStatus.ACTIVE:
                raise NotFoundError("No active Tour-Wayva user has that username.")
            return user, user.email.lower()
        email = payload.recipient_email.lower()  # type: ignore[union-attr]  # validated: exactly one is set
        return await users.get_by_email(email), email

    async def _check_invitable(
        self, *, trip_id: uuid.UUID, inviter: User, recipient_user: Optional[User], recipient_email: str
    ) -> None:
        if recipient_email == inviter.email.lower() or (recipient_user and recipient_user.id == inviter.id):
            raise ValidationAppError("You can't invite yourself.")
        if recipient_user is not None and await self.trip_service.repo.get_membership(trip_id, recipient_user.id):
            raise ConflictError("This person is already a member of the trip.")
        if await self.repo.get_live_pending_invitation(
            trip_id=trip_id, email=recipient_email, now=datetime.now(timezone.utc)
        ):
            raise ConflictError("There is already a pending invitation for this person.")

    async def list_for_recipient(self, *, user: User):
        """The invitee's own pending, unexpired invitations (by @username or by email)."""
        return await self.repo.list_pending_for_recipient(
            user_id=user.id, email=user.email, now=datetime.now(timezone.utc)
        )

    async def list_for_trip(self, *, trip_id: uuid.UUID, user_id: uuid.UUID):
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        return await self.repo.list_invitations_for_trip(trip_id)

    async def cancel(self, *, invitation_id: uuid.UUID, actor_id: uuid.UUID) -> TripInvitation:
        invitation = await self.repo.get_invitation(invitation_id)
        if invitation is None:
            raise NotFoundError("Invitation not found.")

        await self.trip_service.get_trip_authorized(
            trip_id=invitation.trip_id, user_id=actor_id, require_editor=True
        )

        if invitation.status != InvitationStatus.PENDING:
            raise ValidationAppError(f"This invitation was already {invitation.status.value}.")

        invitation.status = InvitationStatus.CANCELLED
        await self.repo.save_invitation(invitation)
        await self.db.commit()
        return invitation

    async def accept(self, *, token: str, accepting_user: User) -> TripMember:
        invitation = await self._get_valid_pending_invitation(token, accepting_user)

        member = await self.trip_service.add_member_from_invitation(
            trip_id=invitation.trip_id, user_id=accepting_user.id, role=invitation.role
        )

        invitation.status = InvitationStatus.ACCEPTED
        await self.repo.save_invitation(invitation)
        await self.db.commit()
        return member

    async def reject(self, *, token: str, accepting_user: User) -> TripInvitation:
        invitation = await self._get_valid_pending_invitation(token, accepting_user)
        invitation.status = InvitationStatus.REJECTED
        await self.repo.save_invitation(invitation)
        await self.db.commit()
        return invitation

    async def _get_valid_pending_invitation(self, token: str, accepting_user: User) -> TripInvitation:
        invitation = await self.repo.get_invitation_by_token(token)
        if invitation is None:
            raise NotFoundError("Invitation not found.")

        if invitation.recipient_user_id is not None and invitation.recipient_user_id != accepting_user.id:
            raise ForbiddenError("This invitation was sent to a different account.")
        if invitation.recipient_email.lower() != accepting_user.email.lower():
            raise ForbiddenError("This invitation was sent to a different email address.")

        if invitation.status != InvitationStatus.PENDING:
            raise ValidationAppError(f"This invitation was already {invitation.status.value}.")

        if invitation.expires_at < datetime.now(timezone.utc):
            invitation.status = InvitationStatus.EXPIRED
            await self.repo.save_invitation(invitation)
            await self.db.commit()
            raise ValidationAppError("This invitation has expired.")

        return invitation
