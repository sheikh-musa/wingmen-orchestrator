from __future__ import annotations

import re

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)")
_LINK_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|p)/[A-Za-z0-9_-]+/?(?:\?[^\s]*)?")


def shortcode(url: str | None) -> str | None:
    m = _SHORTCODE_RE.search(url or "")
    return m.group(1) if m else None


def find_links(text: str | None) -> list[str]:
    return _LINK_RE.findall(text or "")
