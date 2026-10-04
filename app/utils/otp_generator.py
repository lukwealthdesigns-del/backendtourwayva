"""Cryptographically secure OTP code and Wayva ID generation."""
from __future__ import annotations

import secrets
import string

from app.core.config import settings
from app.core.constants import WAYVA_ID_PREFIX


def generate_numeric_otp(length: int | None = None) -> str:
    """Generate a cryptographically secure numeric OTP, e.g. '482913'.

    Uses secrets.choice (not random) — OTPs are security-sensitive."""
    length = length or settings.OTP_LENGTH
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_wayva_id() -> str:
    """Generate a Tour-Wayva-issued immutable public user ID, e.g.
    'WAYVA-7F3K9QZC'. This is distinct from the internal UUID primary
    key and is safe to display/share (e.g. in support tickets)."""
    token = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    return f"{WAYVA_ID_PREFIX}-{token}"
