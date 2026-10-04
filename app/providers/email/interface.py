"""Email provider abstraction."""
from __future__ import annotations

from abc import ABC, abstractmethod


class EmailProvider(ABC):
    @abstractmethod
    async def send_otp_email(self, *, to_email: str, first_name: str, otp_code: str, purpose_label: str) -> bool:
        """Send a transactional OTP email. Returns True on success."""
        raise NotImplementedError

    @abstractmethod
    async def send_transactional_email(
        self, *, to_email: str, subject: str, html_content: str, category: str = "transactional"
    ) -> bool:
        raise NotImplementedError
