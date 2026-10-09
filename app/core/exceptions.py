"""
Standardized application exceptions.

Every API error response follows the shape:
    { "error_code": str, "message": str, "request_id": str | None, "details": dict | None }

Internal details (stack traces, SQL errors, provider credentials) are
NEVER surfaced to the client — see app.core.middleware exception handlers.
"""
from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """Base class for all controlled application errors."""

    status_code: int = 400
    error_code: str = "app_error"

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class ValidationAppError(AppError):
    status_code = 422
    error_code = "validation_error"


class LocationUnavailableError(AppError):
    """No location could be worked out (no coordinates sent, and neither the IP address nor the
    account's country could be resolved). The client should ask the user for permission to use
    the device's location, or let them type a place."""

    status_code = 422
    error_code = "location_unavailable"


class UnauthorizedError(AppError):
    status_code = 401
    error_code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    error_code = "forbidden"


class PlanLimitError(AppError):
    """The request is valid but beyond what the user's plan allows right now (e.g. a trip longer than their plan plans).
    `details` carries the limits so the app can explain them."""

    status_code = 403
    error_code = "plan_limit"


class GenerationQuotaError(AppError):
    """The user used all itinerary generations their plan includes this month."""

    status_code = 403
    error_code = "generation_quota"


class PaymentRequiredError(AppError):
    """The requested action needs a completed payment (e.g. a paid plan)."""

    status_code = 402
    error_code = "payment_required"


class PaymentProviderError(AppError):
    """The payment provider rejected a request or returned an unusable answer."""

    status_code = 502
    error_code = "payment_provider_error"


class FeatureUnavailableError(AppError):
    """A feature an admin has switched OFF for everyone (kill switch) — a temporary
    503, unlike a plan restriction which is a 403 the user can resolve by upgrading."""

    status_code = 503
    error_code = "feature_unavailable"


class PermanentEmailError(AppError):
    """The email provider rejected a message in a way retrying cannot fix."""

    status_code = 502
    error_code = "email_rejected"


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"


class RateLimitedError(AppError):
    status_code = 429
    error_code = "rate_limited"


class OTPExpiredError(AppError):
    status_code = 400
    error_code = "otp_expired"


class OTPInvalidError(AppError):
    status_code = 400
    error_code = "otp_invalid"


class OTPMaxAttemptsError(AppError):
    status_code = 429
    error_code = "otp_max_attempts"


class ProviderUnavailableError(AppError):
    """Raised when an external provider (Cloudinary, Supabase, Brevo,
    Amadeus, etc.) fails or is not configured. Never fabricate data
    in this case — surface this explicit controlled state instead."""

    status_code = 503
    error_code = "provider_unavailable"
