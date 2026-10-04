"""MockEmailProvider (Master Blueprint §92) — records sent emails
in-memory instead of calling Brevo, so tests can assert on what would
have been sent (e.g. "was an OTP email sent to this address?") without
any network dependency."""
from __future__ import annotations

from dataclasses import dataclass

from app.providers.email.interface import EmailProvider


@dataclass
class SentEmail:
    to_email: str
    subject: str
    html_content: str


class MockEmailProvider(EmailProvider):
    def __init__(self):
        self.sent: list[SentEmail] = []

    async def send_otp_email(self, *, to_email: str, first_name: str, otp_code: str, purpose_label: str) -> bool:
        self.sent.append(
            SentEmail(
                to_email=to_email,
                subject=f"Your Tour-Wayva {purpose_label} code: {otp_code}",
                html_content=f"OTP for {first_name}: {otp_code}",
            )
        )
        return True

    async def send_transactional_email(
        self, *, to_email: str, subject: str, html_content: str, category: str = "transactional"
    ) -> bool:
        self.sent.append(SentEmail(to_email=to_email, subject=subject, html_content=html_content))
        return True

    def find_sent_to(self, email: str) -> list[SentEmail]:
        return [e for e in self.sent if e.to_email == email]
