"""Thin, resilient HTTP access to Google Places (New) and Routes. The server key never leaves the backend."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request, safe_error_text

logger = get_logger(__name__)


def _headers(field_mask: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY, "X-Goog-FieldMask": field_mask}


async def google_request(
    provider: str, method: str, url: str, *, field_mask: str, body: Optional[dict[str, Any]] = None, timeout: float = 10.0,
) -> dict[str, Any]:
    """POST/GET to a Google Maps Platform endpoint. Raises ProviderUnavailableError when unconfigured or failing,
    NotFoundError on 404 - never returns made-up data."""
    if not settings.GOOGLE_MAPS_API_KEY:
        raise ProviderUnavailableError("Google Maps Platform is not configured.")

    async def attempt() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=body, headers=_headers(field_mask))
            resp.raise_for_status()
            return resp.json()

    try:
        return await resilient_request(provider, attempt)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise NotFoundError("Not found.") from exc
        logger.error(f"{provider}_http_error", status=exc.response.status_code, body=safe_error_text(exc))
        raise ProviderUnavailableError("The places and routes service failed. Please try again.") from exc
    except CircuitOpenError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"{provider}_error", error=redact_secrets(exc))
        raise ProviderUnavailableError("The places and routes service failed. Please try again.") from exc
