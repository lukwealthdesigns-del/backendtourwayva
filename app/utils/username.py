"""
Username validation.

Usernames are Tour-Wayva's public identity layer (used for tripmate
invitations, sharing, and future @mention-style features), so rules
are deliberately strict and predictable:

  - 3–20 characters
  - lowercase letters, numbers, and underscores only
  - must start with a letter
  - no consecutive underscores, no leading/trailing underscore
  - reserved names are blocked (admin, support, wayva, etc.)
"""
from __future__ import annotations

import re

from app.core.constants import USERNAME_MAX_LENGTH, USERNAME_MIN_LENGTH

_USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]*[a-z0-9]$|^[a-z][a-z0-9]*$")

RESERVED_USERNAMES = {
    "admin", "administrator", "support", "wayva", "tourwayva", "tour-wayva",
    "root", "system", "moderator", "help", "api", "null", "undefined",
    "security", "billing", "superadmin", "super_admin",
}


class InvalidUsernameError(ValueError):
    pass


def normalize_username(raw_username: str) -> str:
    return (raw_username or "").strip().lower()


def validate_username(raw_username: str) -> str:
    """Return the normalized username if valid, else raise
    InvalidUsernameError with a user-facing message."""
    username = normalize_username(raw_username)

    if not (USERNAME_MIN_LENGTH <= len(username) <= USERNAME_MAX_LENGTH):
        raise InvalidUsernameError(
            f"Username must be between {USERNAME_MIN_LENGTH} and {USERNAME_MAX_LENGTH} characters."
        )

    if "__" in username:
        raise InvalidUsernameError("Username cannot contain consecutive underscores.")

    if not _USERNAME_RE.match(username):
        raise InvalidUsernameError(
            "Username must start with a letter and contain only lowercase letters, "
            "numbers, and underscores."
        )

    if username in RESERVED_USERNAMES:
        raise InvalidUsernameError("This username is reserved. Please choose another.")

    return username
