"""URL-safe slug rules for the shared-bot deep-link `?start=<slug>`."""
from __future__ import annotations

import re

_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def validate_slug(slug: str) -> bool:
    """True if slug is a clean, URL-safe storefront slug (1-32 chars,
    lowercase a-z, digits, hyphen, underscore)."""
    return bool(_SLUG_RE.match(slug or ""))


def normalize_slug(raw: str) -> str:
    """Best-effort coercion of free text into a candidate slug.

    Lowercases, trims, collapses whitespace to hyphens, drops any char
    outside [a-z0-9_-]. Caller must still validate_slug() the result.
    """
    s = (raw or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9_-]", "", s)
    return s
