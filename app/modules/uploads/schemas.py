"""Pydantic schemas for upload responses."""
from __future__ import annotations

from pydantic import BaseModel


class UploadResponse(BaseModel):
    url: str
    storage_key: str
    provider: str
    bytes_size: int
    content_type: str
    is_signed_url: bool
