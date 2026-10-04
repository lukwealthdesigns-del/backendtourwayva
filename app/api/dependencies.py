"""
Shared FastAPI dependencies.

Principle followed: never trust the frontend. `get_current_user`
independently decodes and verifies the JWT server-side on every
request — it never trusts a client-supplied user ID.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import TokenType, decode_token
from app.db.models.user import User
from app.db.session import get_db
from app.repositories.user_repository import UserRepository
from app.utils.net import extract_client_ip

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_client_ip(request: Request) -> str | None:
    """Client IP for rate limiting, IP blocks, audit logs and approximate
    localization (never treated as exact location).

    X-Forwarded-For is honoured ONLY for the number of trusted proxies
    configured in TRUSTED_PROXY_HOPS (default 0 = ignore the header),
    because its leftmost entries are attacker-controlled. See
    app/utils/net.py."""
    return extract_client_ip(
        forwarded_for=request.headers.get("x-forwarded-for"),
        peer_host=request.client.host if request.client else None,
        trusted_proxy_hops=settings.TRUSTED_PROXY_HOPS,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise UnauthorizedError("Authentication required.")

    payload = decode_token(credentials.credentials, TokenType.ACCESS)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError) as exc:
        raise UnauthorizedError("Invalid access token.") from exc

    # Per-SESSION revocation check (closes the gap where a single
    # DELETE /users/me/sessions/{id} left THAT device's already-issued
    # access token valid until natural expiry — see
    # app/core/session_blocklist.py for why this is separate from the
    # per-USER `sessions_invalidated_at` check below).
    from app.core.session_blocklist import is_session_blocklisted

    if await is_session_blocklisted(payload.get("sid")):
        raise UnauthorizedError("Session has been revoked. Please log in again.")

    user = await UserRepository(db).get_by_id(user_id)
    if user is None:
        raise UnauthorizedError("User not found.")
    if not user.is_active:
        raise ForbiddenError("Account is not active.")

    # Concrete session-revocation check (Blueprint §51, §77): any
    # token issued before an admin/self-triggered revocation is
    # rejected, even though JWTs themselves are otherwise stateless.
    if user.sessions_invalidated_at is not None:
        # JWT `iat` has whole-second resolution, so compare against the
        # cutoff truncated to the second; otherwise a token issued right
        # AFTER a revocation (e.g. the fresh tokens handed back by
        # change-password) would be rejected because its truncated iat
        # is earlier than the fractional-second revocation timestamp.
        issued_at = payload.get("iat")
        if issued_at is None or int(issued_at) < int(user.sessions_invalidated_at.timestamp()):
            raise UnauthorizedError("Session has been revoked. Please log in again.")

    from app.db.rls import set_rls_user

    await set_rls_user(db, user.id)
    return user


async def get_current_session_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str | None:
    """The `sid` claim of the caller's access token (the UserSession the
    token belongs to), used to mark the current device in session lists.
    Token validity itself is enforced by get_current_user."""
    if credentials is None:
        return None
    payload = decode_token(credentials.credentials, TokenType.ACCESS)
    return payload.get("sid")


async def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    """Alias kept for readability at call sites that want to be
    explicit that activation is required (it always is)."""
    return user


def require_feature(flag):
    """
    Dependency factory gating an endpoint on a resolved feature flag
    (Master Blueprint §48, §50). Usage:

        @router.post(..., dependencies=[Depends(require_feature(FeatureFlag.COMPANION))])

    The decision is EntitlementService's (kill switch > per-user override >
    open-to-all > trial/subscription/free), evaluated fresh on every request:

      * an admin kill switch      -> 503 `feature_unavailable` (temporary; not an upsell)
      * not in the user's plan    -> 403 with details.reason = "not_in_plan"
      * revoked for this user     -> 403 with details.reason = "user_override_revoked"
    """

    async def _dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        from app.modules.entitlements.service import EntitlementService

        await EntitlementService(db).require(current_user.id, flag)

    return _dependency


def rate_limit(*, bucket: str, max_requests: int, window_seconds: int, per: str = "ip"):
    """
    Dependency factory applying a Redis-backed rate limit (Master
    Blueprint §62) to the endpoint it's attached to. Usage:

        @router.post(..., dependencies=[Depends(rate_limit(
            bucket="auth:login", max_requests=10, window_seconds=60, per="ip"
        ))])

    `per="ip"` keys the limit on the caller's IP (for unauthenticated
    endpoints like signup/login); `per="user"` keys it on the
    authenticated user's ID (for authenticated endpoints like
    Companion messages or uploads) — the latter requires
    get_current_user to have already run, so only use `per="user"` on
    routes that also depend on it.
    """

    async def _dependency(
        request: Request,
        client_ip: str | None = Depends(get_client_ip),
    ) -> None:
        from app.core.rate_limit import enforce_rate_limit

        if per == "user":
            # Read the already-decoded user from a prior
            # get_current_user call in this request's dependency
            # chain, without re-verifying the token a second time.
            token_credentials = await _bearer_scheme(request)
            if token_credentials is None:
                raise UnauthorizedError("Authentication required.")
            payload = decode_token(token_credentials.credentials, TokenType.ACCESS)
            key = f"{bucket}:{payload.get('sub', 'unknown')}"
        else:
            key = f"{bucket}:{client_ip or 'unknown'}"

        await enforce_rate_limit(key=key, max_requests=max_requests, window_seconds=window_seconds)

    return _dependency


def require_admin_permission(permission: str):
    """
    Dependency factory gating an endpoint on an admin permission
    (Master Blueprint §55). This is what closes the loop on every
    "temporarily open to any authenticated user" endpoint from
    Phases 2-6 (POST /places, POST /rag/documents, POST /plans,
    PUT /trials/config) now that Phase 7's AdminService exists.
    """

    async def _dependency(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        from app.modules.admin.admin_service import AdminService

        await AdminService(db).require_permission(user_id=current_user.id, permission=permission)

    return _dependency
