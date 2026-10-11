"""
Pure guest-mode rules (stdlib only, unit-tested without the web stack).

  * the session token: opaque, unguessable, only its hash is ever stored
  * what a guest may use (feature set) and when a claim is allowed
  * sliding expiry and abuse-signal hashing
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

GUEST_SESSION_HEADER = "X-Guest-Session"

# Feature flags a guest may use (the FeatureFlag VALUES). Everything else needs an account: voice, attachments,
# memory, flights, live travel, PDF export, collaboration, premium AI. Admin kill switches still apply to guests.
GUEST_FEATURES = frozenset({"DISCOVER", "PLANNER", "COMPANION", "HOTELS", "ACTIVITIES", "WEATHER"})

TOUCH_INTERVAL_SECONDS = 60       # last_activity is written at most this often, so reads do not become writes
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,96}$")


def new_token() -> str:
    """256 bits of randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def looks_like_token(token: Optional[str]) -> bool:
    return bool(token) and bool(_TOKEN_RE.match(token))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_ip(ip: str, secret: str) -> str:
    """A keyed hash of the client IP: lets us count sessions per network without storing the address. An abuse signal only."""
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:32]


def new_expiry(now: datetime, ttl_hours: int) -> datetime:
    return now + timedelta(hours=max(1, int(ttl_hours)))


def should_touch(last_activity_at: datetime, now: datetime) -> bool:
    return (now - last_activity_at).total_seconds() >= TOUCH_INTERVAL_SECONDS


class ClaimDecision(str, Enum):
    CLAIMABLE = "claimable"
    ALREADY_CLAIMED_BY_YOU = "already_claimed_by_you"      # a retry: answer the same way, change nothing
    CLAIMED_BY_ANOTHER = "claimed_by_another"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"


def claim_decision(*, found: bool, status: Optional[str], expires_at: Optional[datetime], claimed_by_user_id, user_id, now: datetime) -> ClaimDecision:
    """May `user_id` claim this guest session right now? Expired work is never transferred; a repeated claim by the same
    user is a harmless retry; another user's claim is refused."""
    if not found:
        return ClaimDecision.NOT_FOUND
    if status == "claimed":
        return ClaimDecision.ALREADY_CLAIMED_BY_YOU if claimed_by_user_id == user_id else ClaimDecision.CLAIMED_BY_ANOTHER
    if expires_at is None or expires_at <= now:
        return ClaimDecision.EXPIRED
    return ClaimDecision.CLAIMABLE


def remaining(limit: int, used: int) -> int:
    return max(0, int(limit) - int(used))
