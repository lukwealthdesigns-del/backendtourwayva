"""Collaboration endpoints (Master Blueprint §43-44)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_feature
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.db.session import get_db
from app.modules.collaboration.comment_service import CommentService
from app.modules.collaboration.invitation_service import InvitationService
from app.modules.collaboration.presenters import (
    comment_response,
    comment_responses,
    invitation_response,
    invitation_responses,
    member_response,
    member_responses,
    my_invitation_responses,
)
from app.modules.collaboration.schemas import (
    ChangeMemberRoleRequest,
    CommentCreateRequest,
    CommentResponse,
    InvitationResponse,
    InviteMemberRequest,
    MyInvitationResponse,
    TripMemberResponse,
    VoteRequest,
    VoteTallyResponse,
)
from app.modules.collaboration.vote_service import VoteService
from app.modules.trips.service import TripService

router = APIRouter(tags=["Collaboration"])


# --- Invitations (nested under /trips) ---

@router.post(
    "/trips/{trip_id}/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature(FeatureFlag.COLLABORATION))]
)
async def invite_member(
    trip_id: uuid.UUID,
    payload: InviteMemberRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invitation = await InvitationService(db).invite(trip_id=trip_id, inviter=current_user, payload=payload)
    return await invitation_response(db, invitation)


@router.get("/trips/{trip_id}/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invitations = await InvitationService(db).list_for_trip(trip_id=trip_id, user_id=current_user.id)
    return await invitation_responses(db, invitations)


@router.delete("/trips/{trip_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_invitation(
    trip_id: uuid.UUID,
    invitation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await InvitationService(db).cancel(invitation_id=invitation_id, actor_id=current_user.id)


@router.get("/invitations/me", response_model=list[MyInvitationResponse])
async def list_my_invitations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The signed-in user's own pending, unexpired invitations (sent by @username or by their
    email). Each carries the `token` the accept/reject endpoints take. Deliberately ungated:
    a lapsed plan must never hide an invitation."""
    invitations = await InvitationService(db).list_for_recipient(user=current_user)
    return await my_invitation_responses(db, invitations)


@router.post("/invitations/{token}/accept", response_model=TripMemberResponse)
async def accept_invitation(
    token: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member = await InvitationService(db).accept(token=token, accepting_user=current_user)
    return await member_response(db, member)


@router.post("/invitations/{token}/reject", response_model=InvitationResponse)
async def reject_invitation(
    token: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    invitation = await InvitationService(db).reject(token=token, accepting_user=current_user)
    return await invitation_response(db, invitation)


# --- Members ---

@router.get("/trips/{trip_id}/members", response_model=list[TripMemberResponse])
async def list_members(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    members = await TripService(db).list_members(trip_id=trip_id, user_id=current_user.id)
    return await member_responses(db, members)


@router.patch("/trips/{trip_id}/members/{user_id}", response_model=TripMemberResponse)
async def change_member_role(
    trip_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: ChangeMemberRoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Owner-only — see TripService.change_member_role for why this
    is narrower than the usual editor-or-above gate."""
    member = await TripService(db).change_member_role(
        trip_id=trip_id, target_user_id=user_id, new_role=payload.role, actor_id=current_user.id
    )
    return await member_response(db, member)


@router.delete("/trips/{trip_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    trip_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await TripService(db).remove_member(trip_id=trip_id, target_user_id=user_id, actor_id=current_user.id)


# --- Comments ---

@router.post("/trips/{trip_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(
    trip_id: uuid.UUID,
    payload: CommentCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    comment = await CommentService(db).add_comment(trip_id=trip_id, user_id=current_user.id, content=payload.content)
    return await comment_response(db, comment)


@router.get("/trips/{trip_id}/comments", response_model=list[CommentResponse])
async def list_comments(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    comments = await CommentService(db).list_comments(trip_id=trip_id, user_id=current_user.id)
    return await comment_responses(db, comments)


@router.delete("/trips/{trip_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    trip_id: uuid.UUID,
    comment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await CommentService(db).delete_comment(comment_id=comment_id, actor_id=current_user.id)


# --- Voting on proposed itinerary changes ---

@router.post("/companion/changes/{change_id}/vote", response_model=VoteTallyResponse)
async def vote_on_change(
    change_id: uuid.UUID,
    payload: VoteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Advisory only — see PendingChangeVote's docstring. Confirming
    or rejecting the change still requires editor/owner access via
    the existing /companion/changes/{id}/confirm|reject endpoints."""
    return await VoteService(db).cast_vote(change_id=change_id, user_id=current_user.id, is_upvote=payload.is_upvote)
