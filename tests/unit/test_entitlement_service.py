"""EntitlementService: cached global toggles, decisions, require() error mapping, admin writes."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from app.core.constants import FREE_TIER_DEFAULT_FLAGS, FeatureFlag
from app.core.exceptions import FeatureUnavailableError, ForbiddenError
from app.db.models.monetization import FeatureFlagSetting
from app.modules.entitlements.service import GLOBAL_FLAGS_CACHE_KEY, EntitlementService
from app.services.cache_service import CacheService


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _Repo:
    def __init__(self):
        self.settings: dict[str, FeatureFlagSetting] = {}
        self.overrides: dict[str, bool] = {}
        self.trial = None
        self.subscription = None
        self.plan = None
        self.queries = {"settings": 0, "override": 0, "trial": 0}

    async def list_flag_settings(self):
        self.queries["settings"] += 1
        return list(self.settings.values())

    async def get_flag_setting(self, flag):
        return self.settings.get(flag)

    async def create_flag_setting(self, setting):
        self.settings[setting.flag.value] = setting
        return setting

    async def save_flag_setting(self, setting):
        return setting

    async def get_override(self, user_id, flag):
        self.queries["override"] += 1
        return NS(is_enabled=self.overrides[flag]) if flag in self.overrides else None

    async def list_overrides_for_user(self, user_id):
        return [NS(flag=FeatureFlag(f), is_enabled=v) for f, v in self.overrides.items()]

    async def get_user_trial(self, user_id):
        self.queries["trial"] += 1
        return self.trial

    async def get_active_subscription(self, user_id):
        return self.subscription

    async def get_plan(self, plan_id):
        return self.plan


@pytest.fixture()
def redis(monkeypatch):
    store = {}

    async def get_json(key):
        return store.get(key)

    async def set_json(key, value, ttl):
        store[key] = value

    async def delete(key):
        store.pop(key, None)

    monkeypatch.setattr(CacheService, "get_json", staticmethod(get_json))
    monkeypatch.setattr(CacheService, "set_json", staticmethod(set_json))
    monkeypatch.setattr(CacheService, "delete", staticmethod(delete))
    return store


def _service():
    service = EntitlementService.__new__(EntitlementService)
    service.db, service.repo = _FakeDB(), _Repo()
    return service


def _run(coro):
    return asyncio.run(coro)


USER = uuid.uuid4()


def test_free_tier_is_the_floor_and_a_plan_adds_features(redis):
    service = _service()
    for flag in FeatureFlag:
        assert _run(service.has_feature(USER, flag)) is (flag in FREE_TIER_DEFAULT_FLAGS)

    service.repo.subscription = NS(plan_id=uuid.uuid4())
    service.repo.plan = NS(is_active=True, included_feature_flags=["HOTELS", "COMPANION"])
    assert _run(service.has_feature(USER, FeatureFlag.HOTELS)) is True
    assert _run(service.has_feature(USER, FeatureFlag.DISCOVER)) is False          # a plan's flags replace the free floor


def test_an_active_trial_is_used_and_an_expired_one_is_ignored(redis):
    service = _service()
    service.repo.trial = NS(expires_at=datetime.now(timezone.utc) + timedelta(days=5), included_feature_flags=["COMPANION"])
    assert _run(service.has_feature(USER, FeatureFlag.COMPANION)) is True
    service.repo.trial = NS(expires_at=datetime.now(timezone.utc) - timedelta(days=1), included_feature_flags=["COMPANION"])
    assert _run(service.has_feature(USER, FeatureFlag.COMPANION)) is False


def test_the_kill_switch_denies_everyone_and_skips_every_other_query(redis):
    service = _service()
    service.repo.settings["HOTELS"] = NS(flag=FeatureFlag.HOTELS, is_killed=True, is_open_to_all=True)
    service.repo.overrides["HOTELS"] = True                                        # even an explicit grant loses
    decision = _run(service.decision(USER, FeatureFlag.HOTELS))
    assert (decision.allowed, decision.reason) == (False, "globally_disabled")
    assert service.repo.queries["override"] == 0 and service.repo.queries["trial"] == 0


def test_open_to_all_grants_without_a_plan_but_a_user_revocation_still_wins(redis):
    service = _service()
    service.repo.settings["COMPANION"] = NS(flag=FeatureFlag.COMPANION, is_killed=False, is_open_to_all=True)
    assert _run(service.has_feature(USER, FeatureFlag.COMPANION)) is True
    assert service.repo.queries["trial"] == 0                                      # no plan lookup needed
    service.repo.overrides["COMPANION"] = False
    assert _run(service.has_feature(USER, FeatureFlag.COMPANION)) is False


def test_global_settings_are_cached_so_flag_checks_do_not_hit_the_database_each_time(redis):
    service = _service()
    for _ in range(5):
        _run(service.has_feature(USER, FeatureFlag.WEATHER))
    assert service.repo.queries["settings"] == 1 and GLOBAL_FLAGS_CACHE_KEY in redis


def test_resolve_all_applies_the_same_precedence_as_a_single_check(redis):
    service = _service()
    service.repo.settings["HOTELS"] = NS(flag=FeatureFlag.HOTELS, is_killed=True, is_open_to_all=False)
    service.repo.settings["COMPANION"] = NS(flag=FeatureFlag.COMPANION, is_killed=False, is_open_to_all=True)
    service.repo.overrides["FLIGHTS"] = True
    flags, source = _run(service.resolve_all(USER))
    assert source == "free" and flags["HOTELS"] is False and flags["COMPANION"] is True and flags["FLIGHTS"] is True
    assert flags["DISCOVER"] is True and flags["MEMORY"] is False
    for flag in FeatureFlag:
        assert flags[flag.value] is _run(service.has_feature(USER, flag)), flag
    assert _run(service.globally_disabled()) == ["HOTELS"]


def test_require_returns_quietly_when_allowed(redis):
    assert _run(_service().require(USER, FeatureFlag.DISCOVER)) is None


def test_a_plan_restriction_is_a_403_the_user_can_resolve_by_upgrading(redis):
    with pytest.raises(ForbiddenError) as caught:
        _run(_service().require(USER, FeatureFlag.HOTELS))
    assert caught.value.details == {"required_feature": "HOTELS", "reason": "not_in_plan"}


def test_a_revoked_user_gets_a_distinct_reason(redis):
    service = _service()
    service.repo.overrides["DISCOVER"] = False
    with pytest.raises(ForbiddenError) as caught:
        _run(service.require(USER, FeatureFlag.DISCOVER))
    assert caught.value.details["reason"] == "user_override_revoked"


def test_a_kill_switch_is_a_503_not_an_upsell(redis):
    service = _service()
    service.repo.settings["DISCOVER"] = NS(flag=FeatureFlag.DISCOVER, is_killed=True, is_open_to_all=False)
    with pytest.raises(FeatureUnavailableError) as caught:
        _run(service.require(USER, FeatureFlag.DISCOVER))
    assert caught.value.status_code == 503 and caught.value.details["reason"] == "globally_disabled"


def test_setting_a_global_state_takes_effect_immediately_and_changes_only_what_was_sent(redis):
    service = _service()
    assert _run(service.has_feature(USER, FeatureFlag.WEATHER)) is True             # populates the cache
    actor = uuid.uuid4()

    setting = _run(service.set_global_state(flag=FeatureFlag.WEATHER, actor_id=actor, is_killed=True,
                                            note="provider outage", note_provided=True))
    assert setting.is_killed and setting.note == "provider outage" and setting.updated_by == actor
    assert GLOBAL_FLAGS_CACHE_KEY not in redis                                        # invalidated for every replica
    assert _run(service.has_feature(USER, FeatureFlag.WEATHER)) is False

    _run(service.set_global_state(flag=FeatureFlag.WEATHER, actor_id=actor, is_open_to_all=True))
    assert setting.is_killed and setting.is_open_to_all and setting.note == "provider outage"   # untouched fields kept
    _run(service.set_global_state(flag=FeatureFlag.WEATHER, actor_id=actor, is_killed=False, note=None, note_provided=True))
    assert not setting.is_killed and setting.note is None
    assert _run(service.has_feature(USER, FeatureFlag.WEATHER)) is True
    assert service.db.commits == 3
