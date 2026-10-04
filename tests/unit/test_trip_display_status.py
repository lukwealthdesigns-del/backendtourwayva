"""Backend-computed trip status: display_status / bucket rules, generation-job interpretation,
the archive flag, and the Google-connected flags on /users/me."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.constants import TripStatus
from app.modules.trips import display_status as ds
from app.modules.trips.generation_state import interpret_job

TODAY = date(2026, 9, 29)
WINDOW = 30


def _status(status, start, end, *, archived=False, generation=None, today=TODAY):
    return ds.compute_display_status(
        status=status, start_date=start, end_date=end, today=today, archived=archived,
        generation_state=generation, upcoming_window_days=WINDOW,
    )


def d(offset):
    return TODAY + timedelta(days=offset)


# ---------------------------------------------------------------------------
# display_status
# ---------------------------------------------------------------------------
def test_new_trip_is_a_draft():
    assert _status(TripStatus.DRAFT, d(10), d(14)) == "draft"


def test_generating_wins_over_the_lifecycle_status():
    assert _status(TripStatus.DRAFT, d(10), d(14), generation="generating") == "generating"
    assert _status(TripStatus.PLANNED, d(10), d(14), generation="generating") == "generating"


def test_failed_only_shows_while_the_trip_is_still_an_empty_draft():
    assert _status(TripStatus.DRAFT, d(10), d(14), generation="failed") == "failed"
    # a failed RE-generation keeps the planned trip's normal badge
    assert _status(TripStatus.PLANNED, d(10), d(14), generation="failed") == "upcoming"


def test_planned_trip_is_upcoming_inside_the_window_and_ready_beyond_it():
    assert _status(TripStatus.PLANNED, d(1), d(5)) == "upcoming"
    assert _status(TripStatus.PLANNED, d(WINDOW), d(WINDOW + 3)) == "upcoming"
    assert _status(TripStatus.PLANNED, d(WINDOW + 1), d(WINDOW + 4)) == "ready"


def test_planned_trip_is_active_from_its_start_date_through_its_end_date():
    assert _status(TripStatus.PLANNED, d(0), d(3)) == "active"
    assert _status(TripStatus.PLANNED, d(-2), d(0)) == "active"
    assert _status(TripStatus.ONGOING, d(-2), d(2)) == "active"


def test_a_trip_whose_end_date_has_passed_is_completed():
    assert _status(TripStatus.PLANNED, d(-10), d(-3)) == "completed"
    assert _status(TripStatus.ONGOING, d(-10), d(-3)) == "completed"


def test_recorded_completed_and_cancelled_trips():
    assert _status(TripStatus.COMPLETED, d(-10), d(-3)) == "completed"
    assert _status(TripStatus.CANCELLED, d(10), d(14)) == "cancelled"


def test_archived_wins_over_everything():
    for st in TripStatus:
        assert _status(st, d(-1), d(3), archived=True, generation="generating") == "archived"


# ---------------------------------------------------------------------------
# buckets (the Trips-page tabs)
# ---------------------------------------------------------------------------
def test_every_display_status_maps_to_one_tab():
    assert {s: ds.bucket_for(s) for s in ds.DISPLAY_STATUSES} == {
        "archived": "archived", "generating": "drafts", "failed": "drafts", "draft": "drafts",
        "ready": "upcoming", "upcoming": "upcoming", "active": "active",
        "completed": "completed", "cancelled": "completed",
    }


def test_all_tab_is_everything_except_archived():
    assert all(ds.in_bucket(s, "all") for s in ds.DISPLAY_STATUSES if s != "archived")
    assert not ds.in_bucket("archived", "all") and ds.in_bucket("archived", "archived")
    assert ds.in_bucket("ready", "upcoming") and ds.in_bucket("upcoming", "upcoming")
    assert not ds.in_bucket("draft", "upcoming")


def test_every_raw_status_and_date_combination_yields_a_known_status():
    for st in TripStatus:
        for start, end in ((d(-9), d(-5)), (d(-1), d(2)), (d(3), d(6)), (d(90), d(95))):
            for gen in (None, "generating", "failed"):
                assert _status(st, start, end, generation=gen) in ds.DISPLAY_STATUSES


# ---------------------------------------------------------------------------
# "today" is the viewer's local date
# ---------------------------------------------------------------------------
def test_today_follows_the_viewers_timezone():
    now = datetime(2026, 9, 29, 23, 30, tzinfo=timezone.utc)        # 00:30 on the 30th in Lagos (UTC+1)
    assert ds.today_for("Africa/Lagos", now=now) == date(2026, 9, 30)
    assert ds.today_for("America/Los_Angeles", now=now) == date(2026, 9, 29)
    assert ds.today_for(None, now=now) == date(2026, 9, 29)
    assert ds.today_for("Not/AZone", now=now) == date(2026, 9, 29)


# ---------------------------------------------------------------------------
# generation job -> trip generation info
# ---------------------------------------------------------------------------
JOB_ID = str(uuid.uuid4())
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _job(status, *, age_seconds=5, error=None):
    return {"job_id": JOB_ID, "status": status, "error": error,
            "created_at": (NOW - timedelta(seconds=age_seconds)).isoformat()}


def test_no_job_or_a_finished_job_means_no_generation_info():
    assert interpret_job(None, now=NOW) is None
    assert interpret_job(_job("succeeded"), now=NOW) is None


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_live_job_is_generating(status):
    info = interpret_job(_job(status), now=NOW)
    assert info.state == "generating" and str(info.job_id) == JOB_ID


def test_a_job_stuck_past_the_lock_window_is_reported_as_failed_and_retryable():
    info = interpret_job(_job("running", age_seconds=60 * 60), now=NOW)
    assert info.state == "failed" and info.error_code == "generation_timed_out" and info.retryable is True


def test_a_failed_job_carries_its_error():
    info = interpret_job(_job("failed", error={"code": "provider_unavailable", "message": "Try later.", "retryable": True}), now=NOW)
    assert (info.state, info.error_code, info.error_message, info.retryable) == ("failed", "provider_unavailable", "Try later.", True)


# ---------------------------------------------------------------------------
# presenter: archive flag + generation flow into the response
# ---------------------------------------------------------------------------
def _trip(status=TripStatus.PLANNED, start=None, end=None):
    return SimpleNamespace(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), title="Lagos to Paris", origin="Lagos", destination="Paris",
        start_date=start or d(5), end_date=end or d(9), travelers=2, budget_amount=None, budget_currency=None,
        status=status, overview=None, current_version_number=1,
        created_at=NOW, updated_at=NOW,
    )


def _present(monkeypatch, trip, membership, generation=None, timezone_name="UTC"):
    from app.modules.trips import presenters

    async def latest(trip_id):
        return generation

    monkeypatch.setattr(presenters, "latest_generation", latest)
    user = SimpleNamespace(timezone=timezone_name)
    return asyncio.run(presenters.trip_response(trip, membership=membership, user=user))


def test_presenter_fills_status_bucket_and_updated_at(monkeypatch):
    resp = _present(monkeypatch, _trip(), SimpleNamespace(archived_at=None))
    assert (resp.display_status, resp.bucket, resp.is_archived, resp.generation) == ("upcoming", "upcoming", False, None)
    assert resp.updated_at == NOW and resp.status == TripStatus.PLANNED


def test_presenter_reports_an_archived_trip_for_the_archiving_member(monkeypatch):
    archived_at = NOW - timedelta(days=1)
    resp = _present(monkeypatch, _trip(), SimpleNamespace(archived_at=archived_at))
    assert (resp.display_status, resp.bucket, resp.is_archived, resp.archived_at) == ("archived", "archived", True, archived_at)


def test_presenter_marks_a_generating_trip_and_returns_the_job_to_poll(monkeypatch):
    info = interpret_job(_job("running"), now=NOW)
    resp = _present(monkeypatch, _trip(TripStatus.DRAFT), SimpleNamespace(archived_at=None), generation=info)
    assert resp.display_status == "generating" and resp.bucket == "drafts" and str(resp.generation.job_id) == JOB_ID


# ---------------------------------------------------------------------------
# /users/me: Google-connected flags
# ---------------------------------------------------------------------------
def _me_user(*, provider="email", subject=None, password_hash="hash"):
    return SimpleNamespace(
        id=uuid.uuid4(), wayva_id="WYV-1", username="lukman", first_name="L", last_name="I", email="l@example.com",
        is_active=True, country="NG", currency="NGN", language="en", timezone="Africa/Lagos", avatar_url=None,
        onboarding_completed=True, created_at=NOW, phone_number_e164="+2348000000000",
        auth_provider=provider, provider_subject_id=subject, password_hash=password_hash,
    )


def test_me_for_an_email_password_account():
    me = __import__("app.modules.users.schemas", fromlist=["MePublic"]).MePublic.from_user(_me_user())
    assert (me.google_connected, me.has_password, me.sign_in_methods) == (False, True, ["password"])


def test_me_for_a_google_only_account():
    from app.modules.users.schemas import MePublic

    me = MePublic.from_user(_me_user(provider="google", subject="g-123", password_hash=None))
    assert (me.google_connected, me.has_password, me.sign_in_methods) == (True, False, ["google"])
    assert me.auth_provider == "google"


def test_me_for_a_password_account_that_linked_google_reports_both():
    from app.modules.users.schemas import MePublic

    me = MePublic.from_user(_me_user(provider="google", subject="g-123", password_hash="hash"))
    assert (me.google_connected, me.has_password, me.sign_in_methods) == (True, True, ["password", "google"])


def test_me_never_exposes_the_subject_id_or_password_hash():
    from app.modules.users.schemas import MePublic

    dumped = MePublic.from_user(_me_user(subject="g-123")).model_dump()
    assert "provider_subject_id" not in dumped and "password_hash" not in dumped
