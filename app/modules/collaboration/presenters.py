"""
Response builders for collaboration data.

Members, comments and invitations reference users by id; the UI needs to show WHO they are. These
helpers attach each user's PUBLIC identity (username, name, avatar — see UserBrief) with a single
batched query per response, so listing N members/comments costs one extra query, not N.

Privacy rule enforced here: an invitation addressed by @username never exposes the invitee's email
to the inviter (Master Blueprint §5: private information is never exposed to another user).
"""
from __future__ import annotations

import uuid
from typing import Iterable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.collaboration import TripComment, TripInvitation
from app.db.models.trip import TripMember
from app.db.models.user import User
from app.modules.collaboration.schemas import (
    CommentResponse,
    InvitationResponse,
    MyInvitationResponse,
    TripMemberResponse,
    UserBrief,
)
from app.repositories.collaboration_repository import CollaborationRepository
from app.repositories.user_repository import UserRepository


async def load_user_briefs(db: AsyncSession, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, UserBrief]:
    users = await UserRepository(db).get_many_by_ids([uid for uid in user_ids if uid is not None])
    return {u.id: UserBrief.model_validate(u) for u in users}


async def member_responses(db: AsyncSession, members: Sequence[TripMember]) -> list[TripMemberResponse]:
    briefs = await load_user_briefs(db, [m.user_id for m in members])
    return [
        TripMemberResponse(user_id=m.user_id, role=m.role, joined_at=m.joined_at, user=briefs.get(m.user_id))
        for m in members
    ]


async def member_response(db: AsyncSession, member: TripMember) -> TripMemberResponse:
    return (await member_responses(db, [member]))[0]


async def comment_responses(db: AsyncSession, comments: Sequence[TripComment]) -> list[CommentResponse]:
    briefs = await load_user_briefs(db, [c.user_id for c in comments])
    return [
        CommentResponse(
            id=c.id, trip_id=c.trip_id, user_id=c.user_id, user=briefs.get(c.user_id),
            content=c.content, created_at=c.created_at,
        )
        for c in comments
    ]


async def comment_response(db: AsyncSession, comment: TripComment) -> CommentResponse:
    return (await comment_responses(db, [comment]))[0]


async def invitation_responses(
    db: AsyncSession, invitations: Sequence[TripInvitation]
) -> list[InvitationResponse]:
    """Inviter-side view. Email invites show the email; @username invites show the invitee's
    public profile instead and NEVER the email."""
    briefs = await load_user_briefs(db, [i.recipient_user_id for i in invitations if i.recipient_user_id])
    out: list[InvitationResponse] = []
    for i in invitations:
        by_username = i.recipient_user_id is not None
        out.append(
            InvitationResponse(
                id=i.id,
                trip_id=i.trip_id,
                recipient_email=None if by_username else i.recipient_email,
                recipient=briefs.get(i.recipient_user_id) if by_username else None,
                role=i.role,
                status=i.status,
                expires_at=i.expires_at,
                created_at=i.created_at,
            )
        )
    return out


async def invitation_response(db: AsyncSession, invitation: TripInvitation) -> InvitationResponse:
    return (await invitation_responses(db, [invitation]))[0]


async def my_invitation_responses(
    db: AsyncSession, invitations: Sequence[TripInvitation]
) -> list[MyInvitationResponse]:
    """Invitee-side view (includes the token the accept/reject endpoints need)."""
    briefs = await load_user_briefs(db, [i.inviter_id for i in invitations])
    trips = await CollaborationRepository(db).get_trips_by_ids([i.trip_id for i in invitations])
    return [
        MyInvitationResponse(
            id=i.id,
            trip_id=i.trip_id,
            trip_title=trips[i.trip_id].title if i.trip_id in trips else None,
            trip_destination=trips[i.trip_id].destination if i.trip_id in trips else None,
            inviter=briefs.get(i.inviter_id),
            role=i.role,
            status=i.status,
            token=i.token,
            expires_at=i.expires_at,
            created_at=i.created_at,
        )
        for i in invitations
    ]


def brief_for(user: User) -> UserBrief:
    return UserBrief.model_validate(user)
