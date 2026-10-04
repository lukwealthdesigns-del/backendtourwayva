"""
Suspicious-login detection (Master Prompt §8 "IPinfo ... suspicious-login analysis").

Pure decision (`is_suspicious`) plus a thin async wrapper that resolves the login IP's
country via IPinfoProvider (Redis-cached — see app/providers/geolocation) and compares it
to the account's own country. A mismatch is INFORMATIONAL, not a block: it never prevents
login, only records a SecurityEvent and notifies the user, since travel is the entire point
of this product and a changed country is routine, not inherently malicious.

Never fires on a user's very first login (nothing to compare against yet) or when either
country is unknown (never fabricate a signal from missing data).
"""
from __future__ import annotations

from typing import Optional


def is_suspicious(*, account_country: Optional[str], login_country: Optional[str], is_first_login: bool) -> bool:
    if is_first_login or not account_country or not login_country:
        return False
    return account_country.strip().upper() != login_country.strip().upper()


async def check_and_record(
    *, db, user, ip_address: Optional[str], is_first_login: bool
) -> Optional[str]:
    """Returns the detected login country if this login was flagged, else None. Best-effort:
    an IPinfo failure degrades to "not flagged", never blocks or delays login."""
    from app.core.constants import NotificationType, SecurityEventSeverity
    from app.modules.notifications.service import NotificationService
    from app.modules.security.service import SecurityService
    from app.providers.geolocation.ipinfo_provider import IPinfoProvider

    try:
        login_country = await IPinfoProvider().lookup_country(ip_address)
    except Exception:  # noqa: BLE001
        return None

    if not is_suspicious(account_country=user.country, login_country=login_country, is_first_login=is_first_login):
        return None

    await SecurityService(db).record_event(
        user_id=user.id, event_type="suspicious_login_new_country", severity=SecurityEventSeverity.WARNING,
        ip_address=ip_address, metadata={"account_country": user.country, "login_country": login_country},
    )
    await NotificationService(db).notify(
        user_id=user.id, notification_type=NotificationType.SECURITY_ALERT,
        title="New sign-in location detected",
        body=f"We noticed a sign-in to your account from {login_country}, which is different from your "
             f"usual location ({user.country}). If this was you, no action is needed. If not, please "
             "change your password immediately.",
        send_email=True,
    )
    return login_country
