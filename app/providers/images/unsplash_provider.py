"""
Unsplash provider — used for Destination/Attraction/Activity/Place
imagery (Master Blueprint §31).

Respects Unsplash's API guidelines: every result carries photographer
attribution (name + profile link), which callers must display
alongside the image per Unsplash's license terms — this is not
optional decoration, it's a usage requirement (§31: "Respect provider
licensing and attribution requirements").

Never fabricates an image: returns None on no-match, raises
ProviderUnavailableError if not configured or the request fails.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request, safe_error_text
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.images.interface import ImageProvider, ImageResult

logger = get_logger(__name__)

_SEARCH_URL = "https://api.unsplash.com/search/photos"


class UnsplashProvider(ImageProvider):
    async def search(self, query: str) -> ImageResult | None:
        if not settings.UNSPLASH_ACCESS_KEY:
            raise ProviderUnavailableError("Image provider is not configured.")

        headers = {"Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}"}
        params = {"query": query, "per_page": 1, "orientation": "landscape"}

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(_SEARCH_URL, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("unsplash", attempt)
        except httpx.HTTPStatusError as exc:
            logger.error("unsplash_http_error", status=exc.response.status_code, body=safe_error_text(exc))
            raise ProviderUnavailableError("Image search failed. Please try again.") from exc
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("unsplash_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Image search failed. Please try again.") from exc

        results = data.get("results") or []
        if not results:
            return None

        top = results[0]
        urls = top.get("urls", {})
        user = top.get("user", {})

        return ImageResult(
            url=urls.get("regular", ""),
            thumbnail_url=urls.get("thumb", ""),
            width=top.get("width", 0),
            height=top.get("height", 0),
            photographer_name=user.get("name", "Unknown"),
            photographer_profile_url=(user.get("links") or {}).get("html", ""),
            source="unsplash",
            attribution_required=True,
            provider="unsplash",
        )
