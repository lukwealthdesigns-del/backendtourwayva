"""
Brevo (formerly Sendinblue) transactional email provider.

Per the requested adjustment, Tour-Wayva does NOT send verification
links or password-reset links. Every auth-related email carries a
short-lived numeric OTP code that the user types back into the app.

Uses Brevo's HTTP "transactional email" API directly via httpx
(no heavyweight SDK dependency) — see:
https://developers.brevo.com/reference/sendtransacemail

EmailLog (Master Blueprint §47) — an optional `db` session can be
passed to the constructor; when present, every send attempt (success
or failure) writes one EmailLog row. `db` is optional and defaults to
None specifically so this provider stays usable in contexts with no
DB session on hand (e.g. early in the auth flow before a user commit)
without a hard dependency — callers with a session (Notification/
Admin-messaging/Invitation services) should always pass it through.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.core.config import settings
from app.core.exceptions import PermanentEmailError, ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.email.interface import EmailProvider
from app.utils.html import esc

logger = get_logger(__name__)

_BREVO_SEND_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"


def _otp_email_html(*, first_name: str, otp_code: str, purpose_label: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;">
      <h2>Tour-Wayva</h2>
      <p>Hi {esc(first_name) or 'there'},</p>
      <p>Your {esc(purpose_label)} code is:</p>
      <p style="font-size:32px;font-weight:bold;letter-spacing:6px;">{esc(otp_code)}</p>
      <p>This code expires in {settings.OTP_EXPIRY_MINUTES} minutes. If you didn't request this,
      you can safely ignore this email.</p>
      <p>— The Tour-Wayva Team</p>
    </div>
    """


class BrevoEmailProvider(EmailProvider):
    def __init__(self, db: Optional[object] = None):
        # Typed as `object` rather than AsyncSession to avoid importing
        # SQLAlchemy into every call site that constructs this provider
        # without a session (there are several — see module docstring).
        self._db = db

    async def send_otp_email(self, *, to_email: str, first_name: str, otp_code: str, purpose_label: str) -> bool:
        subject = f"Your Tour-Wayva {purpose_label} code: {otp_code}"
        html = _otp_email_html(first_name=first_name, otp_code=otp_code, purpose_label=purpose_label)
        return await self.send_transactional_email(
            to_email=to_email, subject=subject, html_content=html, category="otp"
        )

    async def send_transactional_email(
        self, *, to_email: str, subject: str, html_content: str, category: str = "transactional"
    ) -> bool:
        if not settings.BREVO_API_KEY:
            await self._log(to_email, subject, category, success=False, error_message="Email provider is not configured.")
            raise ProviderUnavailableError("Email provider is not configured.")

        payload = {
            "sender": {"name": settings.BREVO_SENDER_NAME, "email": settings.BREVO_SENDER_EMAIL},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_content,
        }
        headers = {
            "accept": "application/json",
            "api-key": settings.BREVO_API_KEY,
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(_BREVO_SEND_EMAIL_URL, json=payload, headers=headers)
                resp.raise_for_status()
            await self._log(to_email, subject, category, success=True)
            return True
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.error("brevo_send_failed", status=status_code)
            await self._log(to_email, subject, category, success=False, error_message=f"HTTP {status_code}")
            if status_code == 429 or status_code >= 500:
                raise ProviderUnavailableError("Could not send email right now. Please try again.") from exc
            # Any other 4xx (bad address, rejected sender, malformed request) will fail the same
            # way every time — retrying only delays the inevitable and hammers the provider.
            raise PermanentEmailError("The email provider rejected this message.") from exc
        except Exception as exc:  # noqa: BLE001
            logger.error("brevo_send_error", error=str(exc))
            await self._log(to_email, subject, category, success=False, error_message=str(exc))
            raise ProviderUnavailableError("Could not send email right now. Please try again.") from exc

    async def _log(
        self, to_email: str, subject: str, category: str, *, success: bool, error_message: Optional[str] = None
    ) -> None:
        if self._db is None:
            return
        try:
            from app.db.models.notification import EmailLog

            # An OTP email's subject CONTAINS the code; logging it would store the code in the
            # clear in our own database, undoing the point of hashing OTPs at rest.
            logged_subject = "[redacted: one-time code email]" if category == "otp" else subject
            self._db.add(
                EmailLog(to_email=to_email, subject=logged_subject, category=category, success=success,
                         error_message=error_message)
            )
            await self._db.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("email_log_write_failed", error=str(exc))
