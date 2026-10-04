"""Pydantic schemas for collaboration endpoints (Master Blueprint §43-44)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.core.constants import InvitationStatus, TripMemberRole
from app.utils.username import InvalidUsernameError, validate_username


class UserBrief(BaseModel):
    """The public identity of a Tour-Wayva user — what other trip members are allowed to see.

    Deliberately excludes email, phone, country and everything else private."""

    id: uuid.UUID
    username: str
    first_name: str
    last_name: str
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}


class InviteMemberRequest(BaseModel):
    """Invite by email OR by @username — exactly one of the two."""

    recipient_email: Optional[EmailStr] = None
    recipient_username: Optional[str] = Field(
        default=None, description="A Tour-Wayva username, with or without the leading '@'."
    )
    role: TripMemberRole = TripMemberRole.VIEWER

    @model_validator(mode="after")
    def _exactly_one_recipient(self) -> "InviteMemberRequest":
        has_email = self.recipient_email is not None
        has_username = self.recipient_username is not None and self.recipient_username.strip() != ""
        if has_email == has_username:
            raise ValueError("Provide exactly one of recipient_email or recipient_username.")
        if has_username:
            raw = self.recipient_username.strip().lstrip("@")
            try:
                self.recipient_username = validate_username(raw)
            except InvalidUsernameError as exc:
                raise ValueError(str(exc)) from exc
        else:
            self.recipient_username = None
        return self

    @model_validator(mode="after")
    def _cannot_invite_as_owner(self) -> "InviteMemberRequest":
        if self.role == TripMemberRole.OWNER:
            raise ValueError("Cannot invite someone directly as owner.")
        return self


class InvitationResponse(BaseModel):
    """What the INVITER (and other trip members) see.

    For an email invite: `recipient_email` is set and `recipient` is null.
    For an @username invite: `recipient` is the invitee's public profile and `recipient_email`
    is null — the invitee's email is never revealed to the inviter."""

    id: uuid.UUID
    trip_id: uuid.UUID
    recipient_email: Optional[str] = None
    recipient: Optional[UserBrief] = None
    role: TripMemberRole
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class MyInvitationResponse(BaseModel):
    """What the INVITEE sees in GET /invitations/me. `token` is what the accept/reject endpoints
    take — it is only ever shown to the account the invitation was addressed to."""

    id: uuid.UUID
    trip_id: uuid.UUID
    trip_title: Optional[str] = None
    trip_destination: Optional[str] = None
    inviter: Optional[UserBrief] = None
    role: TripMemberRole
    status: InvitationStatus
    token: str
    expires_at: datetime
    created_at: datetime


class TripMemberResponse(BaseModel):
    user_id: uuid.UUID
    role: TripMemberRole
    joined_at: datetime
    user: Optional[UserBrief] = None

    model_config = {"from_attributes": True}


class ChangeMemberRoleRequest(BaseModel):
    role: TripMemberRole


class CommentCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class CommentResponse(BaseModel):
    id: uuid.UUID
    trip_id: uuid.UUID
    user_id: uuid.UUID
    user: Optional[UserBrief] = None
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VoteRequest(BaseModel):
    is_upvote: bool


class VoteTallyResponse(BaseModel):
    change_id: uuid.UUID
    upvotes: int
    downvotes: int
    your_vote: Optional[bool] = None
