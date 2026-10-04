"""Google identity verification abstraction (Google OAuth sign-in)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GoogleIdentity:
    """The verified claims we use from a Google ID token. Only what
    Tour-Wayva needs is kept — no other Google data is stored."""

    subject: str          # Google account id (`sub`) — stable, unique per Google account
    email: str
    email_verified: bool
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None


class GoogleIdentityVerifier(ABC):
    @abstractmethod
    async def verify(self, id_token: str) -> GoogleIdentity:
        """Validate the ID token's signature, issuer, audience and expiry
        and return the identity it proves. Raises UnauthorizedError for an
        invalid token and ProviderUnavailableError if Google sign-in is not
        configured or Google's keys cannot be fetched."""
        raise NotImplementedError
