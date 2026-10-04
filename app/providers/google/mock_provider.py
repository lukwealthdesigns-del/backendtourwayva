"""MockGoogleIdentityVerifier (Master Prompt §92) — maps fake token
strings to identities so auth tests never touch Google."""
from __future__ import annotations

from app.core.exceptions import UnauthorizedError
from app.providers.google.interface import GoogleIdentity, GoogleIdentityVerifier


class MockGoogleIdentityVerifier(GoogleIdentityVerifier):
    def __init__(self, identities: dict[str, GoogleIdentity] | None = None):
        self.identities = identities or {}

    async def verify(self, id_token: str) -> GoogleIdentity:
        identity = self.identities.get(id_token)
        if identity is None:
            raise UnauthorizedError("Invalid Google token.")
        return identity
