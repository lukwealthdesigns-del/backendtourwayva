"""
Parses the HTTP `Accept-Language` header (RFC 7231 §5.3.5) — pure, no I/O.

Used as a localization signal (Master Prompt §8): a browser's stated locale often carries
BOTH a language and a region ("fr-CA", "pt-BR", "en-US"), which is more specific than an
IP-derived country and free to read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TAG = re.compile(r"^\s*([a-zA-Z]{2,8})(?:-([a-zA-Z]{2}))?[a-zA-Z0-9-]*\s*(?:;\s*q\s*=\s*([01](?:\.\d+)?))?\s*$")


@dataclass(frozen=True)
class LanguageTag:
    language: str            # lowercase ISO 639-1/2, e.g. "en", "fr"
    region: "str | None"     # uppercase ISO 3166-1 alpha-2, e.g. "US", or None
    quality: float


def parse_accept_language(header: "str | None") -> list[LanguageTag]:
    """Returns tags sorted by quality, highest first. Malformed segments are skipped rather
    than raising — a header is client-supplied input and must never break a request."""
    if not header:
        return []
    tags: list[LanguageTag] = []
    for raw in header.split(","):
        match = _TAG.match(raw)
        if not match:
            continue
        language, region, quality = match.groups()
        if language.lower() == "*":
            continue
        try:
            q = float(quality) if quality is not None else 1.0
        except ValueError:
            q = 1.0
        tags.append(LanguageTag(language=language.lower(), region=region.upper() if region else None, quality=q))
    return sorted(tags, key=lambda t: t.quality, reverse=True)


def best_language(header: "str | None") -> "str | None":
    """The highest-quality language code (e.g. "fr" from "fr-CA"), or None."""
    tags = parse_accept_language(header)
    return tags[0].language if tags else None


def country_hint(header: "str | None") -> "str | None":
    """A region subtag from the highest-quality tag that CARRIES one (e.g. "US" from
    "en-US"). Many browsers send a bare language ("en") with no region — that yields None,
    since a language alone is not a country signal."""
    for tag in parse_accept_language(header):
        if tag.region:
            return tag.region
    return None
