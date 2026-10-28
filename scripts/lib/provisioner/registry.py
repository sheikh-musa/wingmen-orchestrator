"""registry.py — the only code that maps a store alias to its project ref.

op#78463 finding 1 (plan §0): the ref is NEVER hand-typed anywhere — the
plan's first draft spelled the ceayj ref wrong seven times (z at char 11),
and the root cause was hand-typing, not the slip. The ref is loaded from
docs/data-store-registry.md, the single place it is ever spelled, and a
unit test pins the loader against an independent parse of the same row
(plus a package-wide no-ref-literal scan).
"""
from __future__ import annotations

import re
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parents[3]
REGISTRY_DOC = ORCH_DIR / "docs" / "data-store-registry.md"

# Registry table rows: | **store alias** | `<20-char ref>` | ...
_ROW_RE = re.compile(r"^\| \*\*(?P<alias>[^|]+?)\*\* \| `(?P<ref>[a-z0-9]{20})` \|", re.MULTILINE)


class Refuse(Exception):
    """Refuse, don't warn — the apply_migration / provision_bariq pattern."""


def store_ref(alias: str, registry_doc: Path | None = None) -> str:
    """The project ref for a store alias, loaded from the registry doc.

    Never hardcoded; never guessed: an alias with no registry row is a
    Refuse, and so is a missing registry doc (a store that can't be
    resolved by alias must never fall through to some default)."""
    doc = Path(registry_doc) if registry_doc else REGISTRY_DOC
    if not doc.is_file():
        raise Refuse(f"registry doc not found: {doc}")
    for m in _ROW_RE.finditer(doc.read_text()):
        if m.group("alias").strip() == alias:
            return m.group("ref")
    raise Refuse(f"unknown store alias {alias!r} — no row in {doc.name}")
