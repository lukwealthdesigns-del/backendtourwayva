"""
ImageService — implements Blueprint §31-32: before requesting a new
image from Unsplash, normalize the search key (destination + entity
+ locale) and check the cache; only call the provider on a miss.

This is deliberately separate from the Cloudinary storage provider
(app/providers/storage/cloudinary_provider.py), which handles
*user-controlled* uploads (avatars, and eventually admin-curated
destination images). ImageService is for *discovering* stock imagery
from Unsplash to illustrate destinations/attractions/activities —
the two are complementary, not overlapping.
"""
from __future__ import annotations

from typing import Optional

from app.core.exceptions import NotFoundError
from app.modules.images.schemas import ImageResponse
from app.modules.images.query import image_query_candidates
from app.providers.images.unsplash_provider import UnsplashProvider
from app.services.cache_service import TTL_IMAGE_MISS_SECONDS, TTL_IMAGE_SECONDS, CacheService, image_cache_key

_provider = UnsplashProvider()


class ImageService:
    async def get_or_search(self, entity: str, locale: str = "en") -> ImageResponse:
        cache_key = image_cache_key(entity, locale)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            if cached.get("miss"):
                raise NotFoundError(f"No image found for '{entity}'.")      # a remembered miss: no provider call
            return ImageResponse(**{**cached, "source": "cache"})

        result = None
        for query in image_query_candidates(entity) or [entity]:
            result = await _provider.search(f"{query} {locale}" if locale != "en" else query)
            if result is not None:
                break
        if result is None:
            await CacheService.set_json(cache_key, {"miss": True}, TTL_IMAGE_MISS_SECONDS)
            raise NotFoundError(f"No image found for '{entity}'.")

        response = ImageResponse(
            url=result.url,
            thumbnail_url=result.thumbnail_url,
            width=result.width,
            height=result.height,
            photographer_name=result.photographer_name,
            photographer_profile_url=result.photographer_profile_url,
            attribution_required=result.attribution_required,
            source="live",
            provider=result.provider,
        )
        await CacheService.set_json(cache_key, response.model_dump(), TTL_IMAGE_SECONDS)
        return response
