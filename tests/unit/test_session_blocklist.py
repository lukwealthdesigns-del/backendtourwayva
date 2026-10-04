"""Per-session immediate revocation (closes the ≤30-min stale-access-token
gap on single-session revoke — see app/core/session_blocklist.py).
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

import app.api.dependencies as dependencies_module
import app.core.session_blocklist as blocklist_module
from app.core.exceptions import UnauthorizedError
from app.core.session_blocklist import blocklist_session, is_session_blocklisted


def _run(coro):
    return asyncio.run(coro)


class _FakeRedisStore:
    """In-memory stand-in for CacheService.get_raw/set_raw — enough to
    exercise the blocklist module's own logic without a real Redis."""

    def __init__(self):
        self.data: dict[str, str] = {}

    async def set_raw(self, key, value, ttl_seconds):
        self.data[key] = value

    async def get_raw(self, key):
        return self.data.get(key)


@pytest.fixture()
def fake_redis(monkeypatch):
    store = _FakeRedisStore()
    monkeypatch.setattr(blocklist_module.CacheService, "set_raw", staticmethod(store.set_raw))
    monkeypatch.setattr(blocklist_module.CacheService, "get_raw", staticmethod(store.get_raw))
    return store


# --- session_blocklist module itself ---------------------------------

def test_a_session_is_not_blocklisted_before_being_revoked(fake_redis):
    assert _run(is_session_blocklisted(str(uuid.uuid4()))) is False


def test_blocklisting_a_session_makes_it_report_as_blocklisted(fake_redis):
    session_id = str(uuid.uuid4())
    _run(blocklist_session(session_id))
    assert _run(is_session_blocklisted(session_id)) is True


def test_blocklisting_one_session_does_not_affect_another(fake_redis):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    _run(blocklist_session(a))
    assert _run(is_session_blocklisted(a)) is True
    assert _run(is_session_blocklisted(b)) is False


def test_no_sid_claim_is_never_treated_as_blocklisted(fake_redis):
    # A token that predates the `sid` claim (or a malformed one) must not
    # be rejected by THIS check — decode_token/other checks handle that.
    assert _run(is_session_blocklisted(None)) is False
    assert _run(is_session_blocklisted("")) is False


# --- get_current_user wiring ------------------------------------------

class _FakeCredentials:
    def __init__(self, token="irrelevant"):
        self.credentials = token


class _ExplodingUserRepo:
    """Proves get_current_user checks the blocklist BEFORE ever touching
    the database — a revoked session shouldn't cost a DB round trip."""

    def __init__(self, db):
        pass

    async def get_by_id(self, user_id):
        raise AssertionError("UserRepository must not be queried for a blocklisted session")


def test_get_current_user_rejects_a_blocklisted_session_without_querying_the_db(monkeypatch, fake_redis):
    session_id = str(uuid.uuid4())
    _run(blocklist_session(session_id))

    monkeypatch.setattr(
        dependencies_module, "decode_token",
        lambda token, expected_type: {"sub": str(uuid.uuid4()), "sid": session_id, "iat": 0},
    )
    monkeypatch.setattr(dependencies_module, "UserRepository", _ExplodingUserRepo)

    with pytest.raises(UnauthorizedError):
        _run(dependencies_module.get_current_user(credentials=_FakeCredentials(), db=object()))


def test_get_current_user_allows_a_non_blocklisted_session_through_to_the_db(monkeypatch, fake_redis):
    session_id = str(uuid.uuid4())
    user_id = uuid.uuid4()

    class _FakeUser:
        id = user_id
        is_active = True
        sessions_invalidated_at = None

    class _FakeUserRepo:
        def __init__(self, db):
            pass

        async def get_by_id(self, uid):
            assert uid == user_id
            return _FakeUser()

    monkeypatch.setattr(
        dependencies_module, "decode_token",
        lambda token, expected_type: {"sub": str(user_id), "sid": session_id, "iat": 0},
    )
    monkeypatch.setattr(dependencies_module, "UserRepository", _FakeUserRepo)

    result = _run(dependencies_module.get_current_user(credentials=_FakeCredentials(), db=object()))
    assert result.id == user_id


# --- Wiring into revoke_session / logout --------------------------------

def test_revoke_session_blocklists_the_session_id(monkeypatch, fake_redis):
    import app.modules.users.service as users_service_module

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    class _FakeSession:
        id = session_id
        user_id = user_id
        revoked_at = None
        revoked_reason = None

    class _FakeSessionRepo:
        def __init__(self, db):
            pass

        async def get_for_user(self, sid, uid):
            assert sid == session_id and uid == user_id
            return _FakeSession()

        async def save(self, session):
            pass

    class _FakeDB:
        async def commit(self):
            pass

    service = users_service_module.UserService.__new__(users_service_module.UserService)
    service.db = _FakeDB()
    service.session_repo = _FakeSessionRepo(None)

    _run(service.revoke_session(user_id=user_id, session_id=session_id))
    assert _run(is_session_blocklisted(str(session_id))) is True


def test_logout_blocklists_the_sessions_id(monkeypatch, fake_redis):
    import app.modules.auth.service as auth_service_module

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    class _FakeSession:
        id = session_id
        user_id = user_id
        revoked_at = None
        revoked_reason = None

    class _FakeSessionRepo:
        def __init__(self, db):
            pass

        async def get_by_any_hash(self, hashed):
            return _FakeSession()

        async def save(self, session):
            pass

    class _FakeDB:
        async def commit(self):
            pass

    monkeypatch.setattr(
        auth_service_module, "decode_token",
        lambda token, expected_type: {"sub": str(user_id), "jti": "some-jti"},
    )
    monkeypatch.setattr(auth_service_module, "hash_token_id", lambda jti: "hashed")

    service = auth_service_module.AuthService.__new__(auth_service_module.AuthService)
    service.db = _FakeDB()
    service.session_repo = _FakeSessionRepo(None)

    _run(service.logout("some-refresh-token"))
    assert _run(is_session_blocklisted(str(session_id))) is True
