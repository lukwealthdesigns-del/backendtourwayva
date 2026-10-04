"""
Regression tests for the security/correctness fixes (batch 1 & 2).

They use small in-memory fakes instead of a database, so they run in CI
without Postgres/Redis. Each test names the vulnerability it locks down.
"""
from __future__ import annotations

import asyncio
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.core.config import Settings, settings
from app.core.constants import AuthProvider, OTPPurpose, UserRole, UserStatus
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    OTPInvalidError,
    OTPMaxAttemptsError,
    PaymentRequiredError,
    ProviderUnavailableError,
    UnauthorizedError,
    ValidationAppError,
)
from app.core.security import TokenType, create_refresh_token, decode_token, hash_password, hash_token_id
from app.db.base import Base
from app.db.models.monetization import Plan
from app.db.models.otp import OTPCode
from app.db.models.session import UserSession
from app.db.models.user import User
from app.modules.auth.otp_service import OTPService
from app.modules.auth.service import AuthService
from app.modules.subscriptions.service import SubscriptionService
from app.modules.uploads.service import UploadService
from app.modules.users.account_service import anonymized_identity
from app.providers.google.interface import GoogleIdentity
from app.providers.google.mock_provider import MockGoogleIdentityVerifier
from app.providers.malware.mock_provider import EICAR_MARKER, MockMalwareScanner


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------
class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _StubSecurity:
    events: list[dict] = []

    def __init__(self, db):
        pass

    async def record_event(self, **kwargs):
        _StubSecurity.events.append(kwargs)


@pytest.fixture(autouse=True)
def _stub_security(monkeypatch):
    _StubSecurity.events = []
    monkeypatch.setattr("app.modules.security.service.SecurityService", _StubSecurity)


# ---------------------------------------------------------------------------
# Enum persistence — the ORM must write the same labels the migrations create
# ---------------------------------------------------------------------------
def test_every_enum_column_persists_values_not_member_names():
    import app.db.models  # noqa: F401  (registers every model on Base.metadata)

    checked = 0
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, sa.Enum) and column.type.enum_class is not None:
                expected = [member.value for member in column.type.enum_class]
                assert list(column.type.enums) == expected, f"{table.name}.{column.name}"
                checked += 1
    assert checked >= 25


# ---------------------------------------------------------------------------
# Startup guards (unsafe defaults)
# ---------------------------------------------------------------------------
def _prod(**overrides):
    values = dict(
        ENVIRONMENT="production",
        SECRET_KEY="k" * 48,
        DEBUG=False,
        BACKEND_CORS_ORIGINS=["https://app.tourwayva.com"],
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_settings_accept_a_safe_configuration():
    assert _prod().ENVIRONMENT == "production"


@pytest.mark.parametrize(
    "overrides",
    [
        {"SECRET_KEY": "short"},
        {"SECRET_KEY": "change-me-" + "x" * 40},
        {"BACKEND_CORS_ORIGINS": []},
        {"BACKEND_CORS_ORIGINS": ["*"]},
        {"DEBUG": True},
    ],
)
def test_production_refuses_unsafe_configuration(overrides):
    with pytest.raises(ValueError):
        _prod(**overrides)


def test_unknown_environment_is_rejected():
    with pytest.raises(ValueError):
        _prod(ENVIRONMENT="prod")


# ---------------------------------------------------------------------------
# OTP brute force: the attempt counter must survive the request rollback
# ---------------------------------------------------------------------------
class _FakeOTPRepo:
    def __init__(self, otp):
        self.otp = otp

    async def get_latest_active(self, user_id, purpose):
        return self.otp if self.otp.consumed_at is None else None

    async def save(self, otp):
        return otp


def _make_otp(code="123456"):
    return OTPCode(
        user_id=uuid.uuid4(),
        purpose=OTPPurpose.EMAIL_VERIFICATION,
        code_hash=hash_password(code),
        destination="a@b.co",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        attempts=0,
    )


def _otp_service(otp):
    service = OTPService.__new__(OTPService)
    service.db = _FakeDB()
    service.otp_repo = _FakeOTPRepo(otp)
    service.email_provider = None
    return service


def _verify(service, otp, code):
    return asyncio.run(service.verify(user_id=otp.user_id, purpose=otp.purpose, submitted_code=code))


def test_wrong_otp_increments_attempts_and_commits_before_raising():
    otp = _make_otp()
    service = _otp_service(otp)
    with pytest.raises(OTPInvalidError):
        _verify(service, otp, "000000")
    assert otp.attempts == 1
    assert service.db.commits == 1  # persisted BEFORE the error unwinds get_db's rollback


def test_otp_is_burned_after_max_attempts(monkeypatch):
    monkeypatch.setattr(settings, "OTP_MAX_ATTEMPTS", 3)
    otp = _make_otp()
    service = _otp_service(otp)
    for _ in range(2):
        with pytest.raises(OTPInvalidError):
            _verify(service, otp, "000000")
    with pytest.raises(OTPMaxAttemptsError):
        _verify(service, otp, "000000")
    assert otp.consumed_at is not None
    # Even the CORRECT code no longer works: a new one must be requested.
    with pytest.raises(OTPInvalidError):
        _verify(service, otp, "123456")


def test_correct_otp_is_consumed_without_extra_commit():
    otp = _make_otp()
    service = _otp_service(otp)
    _verify(service, otp, "123456")
    assert otp.consumed_at is not None
    assert service.db.commits == 0


# ---------------------------------------------------------------------------
# Uploads: real content type, size, malware, filename, fail-closed scanning
# ---------------------------------------------------------------------------
class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "doc.pdf"):
        self._buf = io.BytesIO(data)
        self.filename = filename

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


def _validate(data, filename="doc.pdf", *, scanner=None, max_mb=1, mimes=("application/pdf",)):
    service = UploadService(scanner=scanner)
    return asyncio.run(
        service.read_validated(_FakeUpload(data, filename), max_mb=max_mb, allowed_mimes=set(mimes))
    )


def test_upload_uses_detected_type_and_sanitizes_filename():
    scanner = MockMalwareScanner()
    validated = _validate(b"%PDF-1.7 hello", "../../My Trip (v2).pdf", scanner=scanner)
    assert validated.content_type == "application/pdf"
    assert validated.filename == "My_Trip_v2.pdf"
    assert scanner.scanned == [len(b"%PDF-1.7 hello")]


def test_upload_rejects_spoofed_extension_executable():
    with pytest.raises(ValidationAppError):
        _validate(b"MZ\x90\x00 not a pdf", "invoice.pdf")


def test_upload_rejects_oversized_and_empty_files():
    with pytest.raises(ValidationAppError):
        _validate(b"%PDF-" + b"0" * (2 * 1024 * 1024), max_mb=1)
    with pytest.raises(ValidationAppError):
        _validate(b"")


def test_upload_rejects_infected_file():
    with pytest.raises(ValidationAppError):
        _validate(b"%PDF-1.4 " + EICAR_MARKER, scanner=MockMalwareScanner())


def test_upload_fails_closed_when_scan_is_required_but_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_MALWARE_SCAN", True)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "")
    with pytest.raises(ProviderUnavailableError):
        _validate(b"%PDF-1.7 hello", scanner=None)


# ---------------------------------------------------------------------------
# Subscriptions: no free paid plan
# ---------------------------------------------------------------------------
class _FakePlanRepo:
    def __init__(self, plan):
        self.plan = plan

    async def get_plan(self, plan_id):
        return self.plan


def _subscription_service(plan):
    service = SubscriptionService.__new__(SubscriptionService)
    service.repo = _FakePlanRepo(plan)
    return service


def test_paid_plan_cannot_be_self_activated():
    plan = Plan(id=uuid.uuid4(), price_amount=5000.0, price_currency="NGN", is_active=True)
    service = _subscription_service(plan)
    with pytest.raises(PaymentRequiredError):
        asyncio.run(service.subscribe(user_id=uuid.uuid4(), plan_id=plan.id))


def test_free_plan_can_be_self_activated(monkeypatch):
    plan = Plan(id=uuid.uuid4(), price_amount=0.0, price_currency="NGN", is_active=True)
    service = _subscription_service(plan)

    async def _fake_activate(*, user_id, plan):
        return "activated"

    monkeypatch.setattr(service, "_activate", _fake_activate)
    assert asyncio.run(service.subscribe(user_id=uuid.uuid4(), plan_id=plan.id)) == "activated"


# ---------------------------------------------------------------------------
# Refresh-token rotation and re-use detection
# ---------------------------------------------------------------------------
class _FakeSessionRepo:
    def __init__(self, session):
        self.session = session

    async def get_by_any_hash(self, jti_hash):
        s = self.session
        return s if jti_hash in (s.refresh_jti_hash, s.previous_jti_hash) else None

    async def save(self, session):
        return session


class _FakeUserRepo:
    def __init__(self, user=None, by_email=None, by_subject=None):
        self.user = user
        self.by_email = by_email or {}
        self.by_subject = by_subject or {}

    async def get_by_id(self, user_id):
        return self.user

    async def get_by_email(self, email):
        return self.by_email.get(email.lower())

    async def get_by_provider_subject(self, provider, subject):
        return self.by_subject.get(subject)

    async def save(self, user):
        return user


def _active_user():
    return User(
        id=uuid.uuid4(), is_active=True, status=UserStatus.ACTIVE, role=UserRole.USER,
        sessions_invalidated_at=None,
    )


def _session_for(user, jti):
    now = datetime.now(timezone.utc)
    return UserSession(
        id=uuid.uuid4(), user_id=user.id, refresh_jti_hash=hash_token_id(jti), previous_jti_hash=None,
        rotated_at=None, last_used_at=now, expires_at=now + timedelta(days=30), revoked_at=None,
    )


def _auth_service(user=None, session=None, **repo_kwargs):
    service = AuthService.__new__(AuthService)
    service.db = _FakeDB()
    service.user_repo = _FakeUserRepo(user, **repo_kwargs)
    service.session_repo = _FakeSessionRepo(session)
    return service


def _refresh(service, token):
    return asyncio.run(service.refresh_session(token))


def test_refresh_rotates_and_old_token_stops_working():
    user, jti = _active_user(), str(uuid.uuid4())
    session = _session_for(user, jti)
    service = _auth_service(user, session)

    old_token = create_refresh_token(str(user.id), jti=jti)
    access, new_refresh = _refresh(service, old_token)

    assert access and new_refresh != old_token
    assert session.previous_jti_hash == hash_token_id(jti)
    # Immediately re-presenting the OLD token (client retry) is refused but
    # does NOT kill the session (inside the grace window).
    with pytest.raises(UnauthorizedError):
        _refresh(service, old_token)
    assert session.revoked_at is None
    # ...and the new token still works.
    _refresh(service, new_refresh)


def test_reusing_a_rotated_token_after_the_grace_window_revokes_the_session():
    user, jti = _active_user(), str(uuid.uuid4())
    session = _session_for(user, jti)
    service = _auth_service(user, session)

    old_token = create_refresh_token(str(user.id), jti=jti)
    _, new_refresh = _refresh(service, old_token)
    session.rotated_at = datetime.now(timezone.utc) - timedelta(seconds=settings.REFRESH_REUSE_GRACE_SECONDS + 60)

    with pytest.raises(UnauthorizedError):
        _refresh(service, old_token)  # stolen/copied token replayed

    assert session.revoked_at is not None
    assert session.revoked_reason == "refresh_token_reuse"
    assert [e["event_type"] for e in _StubSecurity.events] == ["refresh_token_reuse_detected"]
    with pytest.raises(UnauthorizedError):
        _refresh(service, new_refresh)  # the whole lineage is dead


def test_revoke_all_sessions_also_blocks_refresh_tokens():
    """Regression: refresh tokens used to ignore sessions_invalidated_at, so a
    revoked user could keep minting fresh access tokens for 30 days."""
    user, jti = _active_user(), str(uuid.uuid4())
    session = _session_for(user, jti)
    service = _auth_service(user, session)
    token = create_refresh_token(str(user.id), jti=jti)

    user.sessions_invalidated_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    with pytest.raises(UnauthorizedError):
        _refresh(service, token)


def test_refresh_rejects_suspended_user():
    user, jti = _active_user(), str(uuid.uuid4())
    user.status = UserStatus.SUSPENDED
    service = _auth_service(user, _session_for(user, jti))
    with pytest.raises(UnauthorizedError):
        _refresh(service, create_refresh_token(str(user.id), jti=jti))


# ---------------------------------------------------------------------------
# Google sign-in account resolution
# ---------------------------------------------------------------------------
_IDENTITY = GoogleIdentity(
    subject="google-sub-1", email="ada@example.com", email_verified=True,
    given_name="Ada", family_name="Lovelace", picture="https://lh3.googleusercontent.com/a.jpg",
)


def _google_service(**repo_kwargs):
    service = AuthService.__new__(AuthService)
    service.db = _FakeDB()
    service.user_repo = _FakeUserRepo(**repo_kwargs)
    service.google_verifier = MockGoogleIdentityVerifier({"good-token": _IDENTITY})

    async def _no_trial(user):
        return None

    service._start_trial_best_effort = _no_trial
    return service


def _google(service):
    return asyncio.run(service.google_authenticate(id_token="good-token"))


def test_invalid_google_token_is_rejected():
    service = _google_service()
    with pytest.raises(UnauthorizedError):
        asyncio.run(service.google_authenticate(id_token="forged"))


def test_new_google_identity_must_complete_profile_with_signed_token():
    result = _google(_google_service())
    assert result.user is None and result.signup_token
    claims = decode_token(result.signup_token, TokenType.GOOGLE_SIGNUP)
    assert claims["sub"] == "google-sub-1" and claims["email"] == "ada@example.com"
    assert result.prefill["first_name"] == "Ada"


def test_google_signup_token_cannot_be_used_as_an_access_token():
    result = _google(_google_service())
    with pytest.raises(UnauthorizedError):
        decode_token(result.signup_token, TokenType.ACCESS)


def test_google_login_activates_unverified_email_signup_and_drops_squatter_password():
    squatter = User(
        id=uuid.uuid4(), email="ada@example.com", is_active=False, status=UserStatus.PENDING,
        password_hash="attacker-chosen-hash", auth_provider=AuthProvider.EMAIL, provider_subject_id=None,
        avatar_url=None, google_profile_picture_url=None,
    )
    service = _google_service(by_email={"ada@example.com": squatter})
    result = _google(service)
    assert result.user is squatter
    assert squatter.is_active and squatter.status == UserStatus.ACTIVE
    assert squatter.password_hash is None            # the unverified password is gone
    assert squatter.provider_subject_id == "google-sub-1"
    assert squatter.auth_provider == AuthProvider.GOOGLE
    assert squatter.avatar_url == _IDENTITY.picture


def test_google_login_links_verified_account_and_keeps_its_password():
    existing = User(
        id=uuid.uuid4(), email="ada@example.com", is_active=True, status=UserStatus.ACTIVE,
        password_hash="real-hash", auth_provider=AuthProvider.EMAIL, provider_subject_id=None,
        avatar_url="https://res.cloudinary.com/x/avatar.png", google_profile_picture_url=None,
    )
    result = _google(_google_service(by_email={"ada@example.com": existing}))
    assert result.user is existing
    assert existing.password_hash == "real-hash"
    assert existing.avatar_url == "https://res.cloudinary.com/x/avatar.png"  # custom avatar not overwritten


def test_google_account_already_linked_to_a_different_google_id_conflicts():
    existing = User(
        id=uuid.uuid4(), email="ada@example.com", is_active=True, status=UserStatus.ACTIVE,
        provider_subject_id="someone-else",
    )
    with pytest.raises(ConflictError):
        _google(_google_service(by_email={"ada@example.com": existing}))


def test_suspended_google_user_is_refused():
    suspended = User(
        id=uuid.uuid4(), email="ada@example.com", is_active=False, status=UserStatus.SUSPENDED,
        provider_subject_id="google-sub-1",
    )
    with pytest.raises(ForbiddenError):
        _google(_google_service(by_subject={"google-sub-1": suspended}))


# ---------------------------------------------------------------------------
# Account deletion: anonymized identity must fit the columns and stay unique
# ---------------------------------------------------------------------------
def test_anonymized_identity_fits_columns_and_is_unique_per_user():
    a, b = anonymized_identity(uuid.uuid4()), anonymized_identity(uuid.uuid4())
    assert len(a["username"]) <= 20 and len(a["phone_number_e164"]) <= 20 and len(a["email"]) <= 255
    assert len(a["phone_country_code"]) <= 4 and len(a["phone_region_code"]) <= 4
    assert a["email"] != b["email"] and a["username"] != b["username"] and a["phone_number_e164"] != b["phone_number_e164"]
    assert "@deleted.example.com" in a["email"]
