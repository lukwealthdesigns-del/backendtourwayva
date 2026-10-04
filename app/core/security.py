"""
Password hashing and JWT issuance/verification.

Principle followed: Never trust the frontend. Tokens are always
generated and verified here, server-side, using SECRET_KEY from
environment configuration — never a value supplied by the client.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.exceptions import UnauthorizedError

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"
    # Short-lived proof that a Google identity was verified, handed to the
    # client between POST /auth/google and POST /auth/google/complete.
    GOOGLE_SIGNUP = "google_signup"


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return _pwd_context.verify(plain_password, password_hash)


def hash_token_id(jti: str) -> str:
    """SHA-256 of a token's `jti`. Only this hash is stored server-side
    (user_sessions), so a database leak does not expose refresh-token ids."""
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def _create_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: Optional[dict[str, Any]] = None,
    jti: Optional[str] = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
        "jti": jti or str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, extra_claims: Optional[dict[str, Any]] = None) -> str:
    return _create_token(
        subject=user_id,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims=extra_claims,
    )


def create_refresh_token(user_id: str, *, jti: Optional[str] = None) -> str:
    return _create_token(
        subject=user_id,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        jti=jti,
    )


def create_google_signup_token(claims: dict[str, Any]) -> str:
    """`claims` come from a Google ID token we already verified. Subject
    is the Google account id. Signed with our SECRET_KEY, so the client
    cannot alter the verified identity between the two signup steps."""
    return _create_token(
        subject=str(claims["sub"]),
        token_type=TokenType.GOOGLE_SIGNUP,
        expires_delta=timedelta(minutes=settings.GOOGLE_SIGNUP_TOKEN_EXPIRE_MINUTES),
        extra_claims={k: v for k, v in claims.items() if k != "sub"},
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise UnauthorizedError("Invalid or expired token.") from exc

    if payload.get("type") != expected_type.value:
        raise UnauthorizedError("Invalid token type.")

    return payload
