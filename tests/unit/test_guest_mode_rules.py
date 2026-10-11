"""Guest mode: the pure rules, and a contract test that pins WHICH endpoints a guest can reach (no database needed)."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.entitlements.rules import GLOBALLY_DISABLED, GUEST_ACCOUNT_REQUIRED, GlobalFlagState, decide_guest
from app.modules.guest import rules
from app.modules.planning.policy_rules import PolicyConfig, effective_limits, guest_limits

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---- token ----
def test_tokens_are_long_unique_and_only_their_hash_is_stable():
    a, b = rules.new_token(), rules.new_token()
    assert a != b and rules.looks_like_token(a) and len(a) >= 32
    assert rules.hash_token(a) == rules.hash_token(a) != rules.hash_token(b) and len(rules.hash_token(a)) == 64
    assert a not in rules.hash_token(a)


@pytest.mark.parametrize("bad", [None, "", "short", "has spaces " * 6, "x" * 200, "../../etc/passwd" + "a" * 30])
def test_malformed_tokens_are_rejected_before_any_lookup(bad):
    assert not rules.looks_like_token(bad)


def test_ip_hash_is_keyed_and_does_not_contain_the_address():
    h = rules.hash_ip("203.0.113.9", "secret")
    assert "203" not in h and h == rules.hash_ip("203.0.113.9", "secret") and h != rules.hash_ip("203.0.113.9", "other")


# ---- sliding expiry ----
def test_expiry_slides_and_writes_are_throttled():
    assert rules.new_expiry(NOW, 48) == NOW + timedelta(hours=48)
    assert rules.new_expiry(NOW, 0) == NOW + timedelta(hours=1)          # never expires instantly by misconfiguration
    assert not rules.should_touch(NOW, NOW + timedelta(seconds=10))
    assert rules.should_touch(NOW, NOW + timedelta(seconds=61))


# ---- claiming ----
U1, U2 = "user-1", "user-2"


@pytest.mark.parametrize("kw,expected", [
    (dict(found=False, status=None, expires_at=None, claimed_by_user_id=None), rules.ClaimDecision.NOT_FOUND),
    (dict(found=True, status="active", expires_at=NOW + timedelta(hours=1), claimed_by_user_id=None), rules.ClaimDecision.CLAIMABLE),
    (dict(found=True, status="active", expires_at=NOW, claimed_by_user_id=None), rules.ClaimDecision.EXPIRED),
    (dict(found=True, status="active", expires_at=NOW - timedelta(days=3), claimed_by_user_id=None), rules.ClaimDecision.EXPIRED),
    (dict(found=True, status="claimed", expires_at=NOW - timedelta(days=3), claimed_by_user_id=U1), rules.ClaimDecision.ALREADY_CLAIMED_BY_YOU),
    (dict(found=True, status="claimed", expires_at=NOW + timedelta(days=3), claimed_by_user_id=U2), rules.ClaimDecision.CLAIMED_BY_ANOTHER),
])
def test_claim_decisions(kw, expected):
    assert rules.claim_decision(user_id=U1, now=NOW, **kw) == expected


# ---- what a guest may use ----
def test_guest_features_are_exactly_the_blueprint_set():
    assert rules.GUEST_FEATURES == {"DISCOVER", "PLANNER", "COMPANION", "HOTELS", "ACTIVITIES", "WEATHER"}


@pytest.mark.parametrize("flag", ["PDF_EXPORT", "COLLABORATION", "MEMORY", "VOICE", "ATTACHMENTS", "FLIGHTS", "LIVE_TRAVEL", "PREMIUM_AI"])
def test_open_to_all_never_widens_what_a_guest_can_use(flag):
    d = decide_guest(global_state=GlobalFlagState(open_to_all=True), flag_value=flag, guest_features=rules.GUEST_FEATURES)
    assert not d.allowed and d.reason == GUEST_ACCOUNT_REQUIRED


def test_guest_allowed_features_and_the_kill_switch():
    assert decide_guest(global_state=GlobalFlagState(), flag_value="PLANNER", guest_features=rules.GUEST_FEATURES).allowed
    d = decide_guest(global_state=GlobalFlagState(killed=True), flag_value="PLANNER", guest_features=rules.GUEST_FEATURES)
    assert not d.allowed and d.reason == GLOBALLY_DISABLED


# ---- planning limits ----
def test_guest_limits_ignore_growth_mode_and_are_tight():
    cfg = PolicyConfig(growth_mode=True, growth_max_days=120, growth_monthly_generations=30)
    g = guest_limits(cfg, max_days=10, generations=2)
    assert g.tier == "guest" and not g.growth and g.max_days == 10 and g.monthly_limit == 2
    assert effective_limits(cfg, is_premium=False).max_days == 120      # signed-in users still get growth limits
    assert guest_limits(cfg, max_days=0, generations=0).monthly_limit == 1   # never "unlimited" (0) by misconfiguration


# ---- contract: which endpoints a guest can reach ----
ROUTERS = Path(__file__).resolve().parents[2] / "app" / "api" / "routers"
GUEST_OK = {
    "discover", "destinations", "location", "weather", "currency", "images", "maps", "hotels", "activities", "entitlements", "planning",
}
GUEST_ENDPOINTS = {
    ("trips", "PATCH", "/{trip_id}"), ("trips", "POST", ""), ("trips", "GET", ""), ("trips", "GET", "/{trip_id}"),
    ("trips", "GET", "/{trip_id}/itinerary"), ("trips", "POST", "/days/{day_id}/items"), ("trips", "PATCH", "/items/{item_id}"),
    ("trips", "PUT", "/items/{item_id}/replace-hotel"), ("trips", "DELETE", "/items/{item_id}"),
    ("companion", "POST", "/conversations"), ("companion", "GET", "/conversations"),
    ("companion", "GET", "/conversations/{conversation_id}/messages"), ("companion", "POST", "/conversations/{conversation_id}/messages"),
    ("companion", "POST", "/conversations/{conversation_id}/messages/stream"),
    ("collaboration", "GET", "/trips/{trip_id}/members"),
    ("trip_extras", "POST", "/{trip_id}/routes"), ("trip_extras", "GET", "/{trip_id}/routes"),
}


def _guest_endpoints():
    found = set()
    for path in ROUTERS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        blocks = re.split(r"(?m)^(?=@(?:router|limits_router)\.)", text)
        for block in blocks[1:]:
            m = re.match(r'@(?:router|limits_router)\.(\w+)\(\s*\n?\s*"([^"]*)"', block)
            if m and "get_current_user_or_guest" in block:
                found.add((path.stem, m.group(1).upper(), m.group(2)))
    return found


def test_only_the_intended_endpoints_accept_a_guest():
    found = _guest_endpoints()
    whole_files = {stem for stem, _m, _p in found if stem in GUEST_OK}
    extra = {e for e in found if e[0] not in GUEST_OK} - GUEST_ENDPOINTS
    missing = GUEST_ENDPOINTS - found
    assert not extra, f"endpoints a guest can reach that were not approved: {sorted(extra)}"
    assert not missing, f"approved guest endpoints that do not accept a guest: {sorted(missing)}"
    assert whole_files <= GUEST_OK


@pytest.mark.parametrize("name", ["payments", "subscriptions", "users", "auth", "memory", "notifications", "saved_places", "attachments",
                                  "uploads", "admin", "flights", "travel_history", "trials", "rag", "places"])
def test_account_only_routers_never_accept_a_guest(name):
    text = (ROUTERS / f"{name}.py").read_text(encoding="utf-8")
    assert "get_current_user_or_guest" not in text, f"{name} must stay account-only"
