"""Single source of truth for password rules.

Used by signup, password reset, and change-password so the rules can
never drift apart again (the reset flow previously skipped the
uppercase/digit checks that signup enforced).

bcrypt only uses the first 72 BYTES of a password (and newer bcrypt
builds refuse longer input outright), so anything beyond 72 bytes is
rejected here instead of surfacing as a 500 from the hashing layer.
"""
from __future__ import annotations

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72


def validate_password_strength(password: str) -> str:
    """Return the password unchanged if it satisfies the rules,
    otherwise raise ValueError with a user-facing message."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long.")
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_BYTES} bytes long.")
    if not any(c.isupper() for c in password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not any(c.isdigit() for c in password):
        raise ValueError("Password must contain at least one number.")
    return password
