"""
Cloudinary provider — used for User Avatars and Destination Images.

Cloudinary is chosen here specifically for its automatic optimization
(format/quality auto-negotiation) and built-in CDN delivery, which
matters for image-heavy surfaces (avatars shown everywhere in the UI,
destination imagery in Discover results).
"""
from __future__ import annotations

import asyncio
from typing import Optional

import cloudinary
import cloudinary.uploader

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.storage.interface import StorageProvider, UploadResult

logger = get_logger(__name__)

_configured = False


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    if not (settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY and settings.CLOUDINARY_API_SECRET):
        raise ProviderUnavailableError("Cloudinary is not configured.")
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )
    _configured = True


class CloudinaryProvider(StorageProvider):
    async def upload(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        folder: str,
        user_id: Optional[str] = None,
    ) -> UploadResult:
        _ensure_configured()

        public_id = None
        if user_id:
            # Deterministic public_id per user for avatars => re-upload replaces the old one.
            public_id = f"{user_id}"

        try:
            result = await asyncio.to_thread(
                cloudinary.uploader.upload,
                file_bytes,
                folder=folder,
                public_id=public_id,
                overwrite=True,
                resource_type="image",
                # Automatic format + quality optimization, served via Cloudinary's CDN.
                transformation=[{"fetch_format": "auto", "quality": "auto"}],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("cloudinary_upload_failed", error=str(exc))
            raise ProviderUnavailableError("Image upload failed. Please try again.") from exc

        return UploadResult(
            url=result["secure_url"],
            storage_key=result["public_id"],
            provider="cloudinary",
            bytes_size=result.get("bytes", len(file_bytes)),
            content_type=content_type,
            is_signed_url=False,
        )

    async def delete(self, storage_key: str) -> bool:
        _ensure_configured()
        try:
            result = await asyncio.to_thread(cloudinary.uploader.destroy, storage_key)
            return result.get("result") == "ok"
        except Exception as exc:  # noqa: BLE001
            logger.error("cloudinary_delete_failed", error=str(exc), storage_key=storage_key)
            return False

    async def get_signed_url(self, storage_key: str, expires_in_seconds: int = 3600) -> str:
        # Cloudinary images served under this setup are public CDN URLs;
        # signed/authenticated delivery can be enabled later if a use case needs it.
        _ensure_configured()
        return cloudinary.CloudinaryImage(storage_key).build_url(secure=True)
