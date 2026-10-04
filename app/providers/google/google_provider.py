"""
Verifies Google ID tokens (from "Sign in with Google" / Google Identity
Services on the frontend) locally against Google's published signing keys.

Verification is done server-side on every call — the frontend's claim
that a user "signed in with Google" is never trusted (Principle 1):

  1. signature (RS256) against Google's JWKS, selected by the token's `kid`
  2. `aud` must equal OUR GOOGLE_CLIENT_ID (a token minted for another
     app is rejected)
  3. `iss` must be accounts.google.com
  4. `exp` must be in the future
  5. the email must be marked verified by Google

Google's keys are cached in-process for the lifetime advertised by the
response's Cache-Control header (default 1 hour) and refetched once if a
token references an unknown `kid` (key rotation).
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

import httpx
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import settings
from app.core.redaction import redact_secrets
from app.core.exceptions import ProviderUnavailableError, UnauthorizedError
from app.core.logging import get_logger
from app.providers.google.interface import GoogleIdentity, GoogleIdentityVerifier

logger = get_logger(__name__)

_GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_DEFAULT_KEY_TTL_SECONDS = 3600

_key_cache: dict[str, Any] = {"keys": [], "expires_at": 0.0}


def _ttl_from_cache_control(header_value: Optional[str]) -> int:
    match = re.search(r"max-age=(\d+)", header_value or "")
    return int(match.group(1)) if match else _DEFAULT_KEY_TTL_SECONDS


class GoogleJWKSVerifier(GoogleIdentityVerifier):
    def __init__(self, client_id: Optional[str] = None):
        self._client_id = client_id or settings.GOOGLE_CLIENT_ID

    async def _fetch_keys(self, *, force: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force and _key_cache["keys"] and now < _key_cache["expires_at"]:
            return _key_cache["keys"]
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(_GOOGLE_CERTS_URL)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("google_jwks_fetch_failed", error=redact_secrets(exc))
            raise ProviderUnavailableError("Google sign-in is temporarily unavailable.") from exc

        keys = response.json().get("keys", [])
        _key_cache["keys"] = keys
        _key_cache["expires_at"] = now + _ttl_from_cache_control(response.headers.get("cache-control"))
        return keys

    async def _key_for(self, kid: str) -> dict[str, Any]:
        for force in (False, True):
            for key in await self._fetch_keys(force=force):
                if key.get("kid") == kid:
                    return key
        raise UnauthorizedError("Invalid Google token.")

    async def verify(self, id_token: str) -> GoogleIdentity:
        if not self._client_id:
            raise ProviderUnavailableError("Google sign-in is not configured.")

        try:
            header = jwt.get_unverified_header(id_token)
        except JWTError as exc:
            raise UnauthorizedError("Invalid Google token.") from exc

        if header.get("alg") != "RS256" or not header.get("kid"):
            raise UnauthorizedError("Invalid Google token.")

        key = await self._key_for(header["kid"])

        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"verify_at_hash": False},
            )
        except ExpiredSignatureError as exc:
            raise UnauthorizedError("Google token has expired. Please sign in again.") from exc
        except JWTError as exc:
            raise UnauthorizedError("Invalid Google token.") from exc

        if claims.get("iss") not in _VALID_ISSUERS:
            raise UnauthorizedError("Invalid Google token.")

        email = (claims.get("email") or "").strip().lower()
        verified = claims.get("email_verified")
        if not email or verified not in (True, "true"):
            raise UnauthorizedError("Your Google account email is not verified.")

        return GoogleIdentity(
            subject=str(claims["sub"]),
            email=email,
            email_verified=True,
            given_name=claims.get("given_name"),
            family_name=claims.get("family_name"),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )
