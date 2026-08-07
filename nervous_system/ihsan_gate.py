"""Ihsan Gate — pure, read-only reader for the ihsan_gate_manifest.

Head of Quality, Phase 1 (CODIFY). See reports/head-of-quality-spec.md §8.

This module loads docs/ihsan-gate-manifest.yaml (the machine-readable projection
of the docs/ihsan-gate.md doctrine) and, given a CHANGE CLASS, returns the gate
items required for that class. Each item is tagged `deterministic`
(auto-checkable) or `judgment` (needs a reviewer).

ZERO ENFORCEMENT / ZERO BEHAVIOR CHANGE (Phase 1 contract):
  - This module has NO side effects: it reads a YAML file and returns data.
  - It does NOT block, gate, merge, deploy, page, or auto-invoke a reviewer.
  - Nothing in any merge/deploy/ship path imports it yet. It is inert doctrine
    made queryable. Enforcement (a fail-closed gate) is Phase 2+, gated on cai.

Reader API (all pure functions):
  load_manifest(path=None)              -> dict   (parsed manifest, cached)
  validate_manifest(manifest=None)      -> list[str]  ([] == internally consistent)
  list_gate_items(manifest=None)        -> dict   ({id: item})
  list_change_classes(manifest=None)    -> list[str]
  resolve_class(change_class, ...)      -> str    (normalized/aliased class key;
                                                   unknown -> default_class)
  required_item_ids(change_class, ...)  -> list[str]
  gate_items_for(change_class, ...)     -> list[dict]  (full item dicts, id-tagged)

Nothing here raises on an unknown change class — an unknown/ambiguous class
resolves to the STRICTEST default (`default_class`, "ambiguous = flag-not-guess").
Malformed manifest paths raise (a missing/broken manifest is a real error, not a
silent default).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import yaml

# docs/ihsan-gate-manifest.yaml, resolved relative to the repo root (this file
# lives at nervous_system/ihsan_gate.py, so repo root is one dir up).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MANIFEST_PATH = os.path.join(_REPO_ROOT, "docs", "ihsan-gate-manifest.yaml")

_VALID_TYPES = frozenset({"deterministic", "judgment"})


def _normalize(name: str) -> str:
    """Normalize a class name for alias matching: lowercase, trim, collapse
    separators. 'UI/frontend', ' UI frontend ', 'ui-frontend' all -> 'ui-frontend'.
    """
    s = str(name).strip().lower()
    for sep in ("/", " ", "_"):
        s = s.replace(sep, "-")
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


@lru_cache(maxsize=8)
def load_manifest(path: Optional[str] = None) -> dict:
    """Load and parse the manifest YAML. Cached by path.

    Raises FileNotFoundError if the manifest is missing and yaml.YAMLError if it
    does not parse — a broken manifest is a loud error, never a silent default.
    Returns a plain dict; callers must not mutate it (it is shared/cached).
    """
    p = path or DEFAULT_MANIFEST_PATH
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"ihsan_gate manifest {p!r} did not parse to a mapping")
    return data


def _manifest(manifest: Optional[dict], path: Optional[str]) -> dict:
    if manifest is not None:
        return manifest
    return load_manifest(path)


def list_gate_items(manifest: Optional[dict] = None, *, path: Optional[str] = None) -> dict:
    """Return {gate_item_id: item_dict} from the manifest."""
    return dict(_manifest(manifest, path).get("gate_items", {}))


def list_change_classes(manifest: Optional[dict] = None, *, path: Optional[str] = None) -> list:
    """Return the list of canonical change-class keys defined in the manifest."""
    return list(_manifest(manifest, path).get("change_classes", {}).keys())


def _alias_index(manifest: dict) -> dict:
    """Build {normalized_name: canonical_class_key} over class keys + aliases."""
    idx: dict = {}
    for key, spec in manifest.get("change_classes", {}).items():
        idx[_normalize(key)] = key
        for alias in (spec or {}).get("aliases", []) or []:
            idx.setdefault(_normalize(alias), key)
    return idx


def resolve_class(
    change_class: str,
    manifest: Optional[dict] = None,
    *,
    path: Optional[str] = None,
) -> str:
    """Resolve an input class name (or alias) to a canonical class key.

    Unknown / ambiguous / None -> the manifest's `default_class` (the strictest).
    This is the "safe default" contract: we never silently under-gate.
    """
    m = _manifest(manifest, path)
    default = m.get("default_class")
    if change_class is None:
        return default
    idx = _alias_index(m)
    return idx.get(_normalize(change_class), default)


def required_item_ids(
    change_class: str,
    manifest: Optional[dict] = None,
    *,
    path: Optional[str] = None,
) -> list:
    """Return the required gate-item IDs (e.g. ['G1','G7','G10']) for a class.

    Unknown class -> the default (strictest) class's items.
    """
    m = _manifest(manifest, path)
    key = resolve_class(change_class, m)
    spec = m.get("change_classes", {}).get(key, {}) or {}
    return list(spec.get("required_items", []))


def gate_items_for(
    change_class: str,
    manifest: Optional[dict] = None,
    *,
    path: Optional[str] = None,
) -> list:
    """Return the full required gate-item dicts for a change class.

    Each returned dict is the manifest item enriched with its `id` and the
    `resolved_class` it was requested under. Read-only projection — the source
    manifest is not mutated. Unknown class -> the strictest default's items.
    """
    m = _manifest(manifest, path)
    key = resolve_class(change_class, m)
    items = m.get("gate_items", {})
    out = []
    for iid in required_item_ids(change_class, m):
        item = items.get(iid)
        if item is None:
            # A dangling reference is a manifest bug; validate_manifest() reports
            # it. Skip here so the reader stays total, never raising on class calls.
            continue
        enriched = dict(item)
        enriched["id"] = iid
        enriched["resolved_class"] = key
        out.append(enriched)
    return out


def deterministic_items(change_class: str, manifest: Optional[dict] = None, *, path: Optional[str] = None) -> list:
    """Required items of type `deterministic` for a class."""
    return [i for i in gate_items_for(change_class, manifest, path=path) if i.get("type") == "deterministic"]


def judgment_items(change_class: str, manifest: Optional[dict] = None, *, path: Optional[str] = None) -> list:
    """Required items of type `judgment` for a class."""
    return [i for i in gate_items_for(change_class, manifest, path=path) if i.get("type") == "judgment"]


def validate_manifest(manifest: Optional[dict] = None, *, path: Optional[str] = None) -> list:
    """Check the manifest is internally consistent. Returns a list of error
    strings; an empty list means valid. Pure — no side effects.

    Checks:
      - `v` (version) present and an int
      - every gate item has a valid `type` (deterministic|judgment) and a title
      - every item referenced by a change class's required_items is defined
      - every item in a class's mandatory_reviews is in that class's required_items
      - `default_class` is a defined change class
      - alias normalization has no collisions across different classes
    """
    m = _manifest(manifest, path)
    errors: list = []

    v = m.get("v")
    if not isinstance(v, int):
        errors.append(f"`v` (version) must be an int, got {v!r}")

    items = m.get("gate_items") or {}
    if not items:
        errors.append("no gate_items defined")
    for iid, spec in items.items():
        spec = spec or {}
        t = spec.get("type")
        if t not in _VALID_TYPES:
            errors.append(f"gate item {iid}: invalid type {t!r} (want deterministic|judgment)")
        if not spec.get("title"):
            errors.append(f"gate item {iid}: missing title")

    classes = m.get("change_classes") or {}
    if not classes:
        errors.append("no change_classes defined")
    for key, spec in classes.items():
        spec = spec or {}
        req = spec.get("required_items", []) or []
        if not req:
            errors.append(f"change class {key!r}: empty required_items")
        for iid in req:
            if iid not in items:
                errors.append(f"change class {key!r}: references undefined gate item {iid!r}")
        for iid in spec.get("mandatory_reviews", []) or []:
            if iid not in req:
                errors.append(
                    f"change class {key!r}: mandatory_review {iid!r} not in its required_items"
                )

    default = m.get("default_class")
    if default not in classes:
        errors.append(f"default_class {default!r} is not a defined change class")

    # Alias-collision check: two different classes claiming the same normalized name.
    seen: dict = {}
    for key, spec in classes.items():
        names = [key] + list((spec or {}).get("aliases", []) or [])
        for n in names:
            norm = _normalize(n)
            if norm in seen and seen[norm] != key:
                errors.append(
                    f"alias collision: {n!r} (normalized {norm!r}) maps to both "
                    f"{seen[norm]!r} and {key!r}"
                )
            else:
                seen.setdefault(norm, key)

    return errors


__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "load_manifest",
    "validate_manifest",
    "list_gate_items",
    "list_change_classes",
    "resolve_class",
    "required_item_ids",
    "gate_items_for",
    "deterministic_items",
    "judgment_items",
]
