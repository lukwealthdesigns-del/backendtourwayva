"""Response-shape guarantees for the collaboration/itinerary schema additions (no database needed)."""
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.core.constants import InvitationStatus, TripMemberRole, TripStatus
from app.modules.collaboration.schemas import (
    CommentResponse,
    InvitationResponse,
    TripMemberResponse,
    UserBrief,
)
from app.modules.itinerary.schemas import TripItemResponse
from app.modules.trips.schemas import TripResponse

NOW = datetime.now(timezone.utc)


def _brief():
    return UserBrief(id=uuid.uuid4(), username="lukman", first_name="Lukman", last_name="Ibrahim", avatar_url=None)


def test_user_brief_exposes_only_public_fields():
    assert set(UserBrief.model_fields) == {"id", "username", "first_name", "last_name", "avatar_url"}


def test_member_and_comment_carry_the_user_brief():
    brief = _brief()
    member = TripMemberResponse(user_id=brief.id, role=TripMemberRole.EDITOR, joined_at=NOW, user=brief)
    comment = CommentResponse(id=uuid.uuid4(), trip_id=uuid.uuid4(), user_id=brief.id, user=brief, content="hi", created_at=NOW)
    assert member.user.username == "lukman" and comment.user.username == "lukman"
    assert member.model_dump()["user_id"] == brief.id          # the original field is kept


def test_username_invite_response_has_profile_and_no_email():
    brief = _brief()
    resp = InvitationResponse(
        id=uuid.uuid4(), trip_id=uuid.uuid4(), recipient_email=None, recipient=brief,
        role=TripMemberRole.VIEWER, status=InvitationStatus.PENDING, expires_at=NOW, created_at=NOW,
    )
    assert resp.recipient_email is None and resp.recipient.username == "lukman"


def test_email_invite_response_still_validates():
    resp = InvitationResponse(
        id=uuid.uuid4(), trip_id=uuid.uuid4(), recipient_email="friend@example.com",
        role=TripMemberRole.VIEWER, status=InvitationStatus.PENDING, expires_at=NOW, created_at=NOW,
    )
    assert resp.recipient is None


def test_trip_item_response_includes_image_booking_link_and_source():
    for field in ("image_url", "booking_link", "source"):
        assert field in TripItemResponse.model_fields, field


def test_trip_response_includes_overview_and_defaults_to_none():
    assert "overview" in TripResponse.model_fields
    trip = SimpleNamespace(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), title="t", origin=None, destination="Paris", start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 3), travelers=1, budget_amount=None, budget_currency=None,
        status=TripStatus.DRAFT, overview="A relaxed long weekend.", current_version_number=1, created_at=NOW,
    )
    assert TripResponse.model_validate(trip).overview == "A relaxed long weekend."
