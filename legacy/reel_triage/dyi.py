from __future__ import annotations

import io
import json
import zipfile

from reel_triage import links


def _walk_strings(obj):
    """Yield every string value anywhere in a nested JSON structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def parse(zip_bytes: bytes) -> list[dict]:
    """Return deduped [{shortcode, url, source}] from a Meta DYI export ZIP.

    saved_posts*.json -> dyi_saved; messages/inbox/**/*.json -> dyi_dm.
    First-seen shortcode wins (saved files iterate before DM files).
    """
    seen: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        ordered = (
            [n for n in names if "saved" in n.lower() and n.endswith(".json")]
            + [n for n in names if "/inbox/" in n and n.endswith(".json")]
        )
        for name in ordered:
            source = "dyi_saved" if "saved" in name.lower() else "dyi_dm"
            try:
                doc = json.loads(z.read(name).decode("utf-8", "replace"))
            except (json.JSONDecodeError, KeyError):
                continue
            for s in _walk_strings(doc):
                for url in links.find_links(s):
                    code = links.shortcode(url)
                    if code and code not in seen:
                        seen[code] = {"shortcode": code, "url": url, "source": source}
    return list(seen.values())
