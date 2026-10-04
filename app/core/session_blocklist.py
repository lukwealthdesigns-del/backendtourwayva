"""Per-session immediate revocation.

`user.sessions_invalidated_at` (checked in api/dependencies.get_current_user)
already gives immediate, no-TTL-needed invalidation for USER-wide events —
logout-all, password change/reset, admin "revoke all sessions" — because it's
a stored cutoff timestamp compared against the token's `iat` on every
request.

That mechanism deliberately can't express "revoke just THIS ONE device":
setting it would log out every session, not one. `DELETE /users/me/sessions/{id}`
(self-service, single-session revoke) needs a narrower tool — this module.

Every access token carries a `sid` claim (the UserSession it belongs to —
see create_access_token call sites in app/modules/auth/service.py). Revoking
one session marks its id in this Redis blocklist with a TTL equal to the
access-token lifetime: by the time the entry expires, any token for that
session would have expired naturally anyway, so nothing needs sweeping up.
Redis being unreachable degrades to "the session row is revoked, but this
one already-issued access token keeps working until it expires naturally"
— i.e. exactly today's pre-fix behavior, not a hard failure (Blueprint §78).
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.services.cache_service import CacheService


def _key(session_id: str) -> str:
    return f"session_revoked:{session_id}"


async def blocklist_session(session_id: str) -> None:
    ttl_seconds = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    await CacheService.set_raw(_key(session_id), "1", ttl_seconds)


async def is_session_blocklisted(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    return await CacheService.get_raw(_key(session_id)) is not None
