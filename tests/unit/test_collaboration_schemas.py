import pytest
from pydantic import ValidationError

from app.core.constants import TripMemberRole
from app.modules.collaboration.schemas import InviteMemberRequest


def test_invite_defaults_to_viewer():
    req = InviteMemberRequest(recipient_email="friend@example.com")
    assert req.role == TripMemberRole.VIEWER


def test_invite_as_editor_allowed():
    req = InviteMemberRequest(recipient_email="friend@example.com", role=TripMemberRole.EDITOR)
    assert req.role == TripMemberRole.EDITOR


def test_invite_as_owner_rejected():
    with pytest.raises(ValidationError):
        InviteMemberRequest(recipient_email="friend@example.com", role=TripMemberRole.OWNER)


def test_invalid_email_rejected():
    with pytest.raises(ValidationError):
        InviteMemberRequest(recipient_email="not-an-email")


# --- @username invitations ---

def test_invite_by_username_normalizes_at_sign_and_case():
    req = InviteMemberRequest(recipient_username="@Lukman_Ibrahim")
    assert req.recipient_username == "lukman_ibrahim"
    assert req.recipient_email is None


def test_invite_by_username_without_at_sign():
    assert InviteMemberRequest(recipient_username="lukman").recipient_username == "lukman"


def test_invite_requires_exactly_one_recipient():
    with pytest.raises(ValidationError):
        InviteMemberRequest()
    with pytest.raises(ValidationError):
        InviteMemberRequest(recipient_email="friend@example.com", recipient_username="lukman")
    with pytest.raises(ValidationError):
        InviteMemberRequest(recipient_username="   ")


def test_invalid_or_reserved_username_rejected():
    for bad in ("ab", "1abc", "has space", "double__underscore", "admin", "@@@"):
        with pytest.raises(ValidationError):
            InviteMemberRequest(recipient_username=bad)


def test_username_invite_as_owner_rejected():
    with pytest.raises(ValidationError):
        InviteMemberRequest(recipient_username="lukman", role=TripMemberRole.OWNER)
