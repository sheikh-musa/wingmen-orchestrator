"""spec.py — the per-client parameterization of what provision_bariq.py
hardcoded (role, tables, org UUID, seed keys), plan §0/§1.

A client bundle renders FROM A SPEC, and the spec validates fail-closed
before anything renders: the store alias must resolve in the registry
(the ref is never carried here), the org UUID must be the documented
lookup-free pin (first 8 hex chars = the client key's first 4 chars —
the convention the live bariq migration established), and table/role
names are DERIVED from client_key so one client's spec can never name
another client's objects.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .registry import Refuse, store_ref

_SPECS_DIR = Path(__file__).resolve().parent / "specs"

# identifier-safe unquoted set only: a hyphen would render invalid DDL
# (cc-quality Minor b, PR#566) — refuse at validation, not at apply-time
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{3,15}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass(frozen=True)
class ClientSpec:
    client_key: str
    alias: str            # store alias — resolved via the registry, never a ref here
    org_uuid: str         # deterministic, lookup-free-pinned org id
    org_name: str         # legal name for the organizations row
    org_uen: str          # Singapore UEN for the organizations row
    org_slug: str         # slug for the organizations row
    seed_keys: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: dict) -> "ClientSpec":
        for field in ("client_key", "alias", "org_uuid", "org_name", "org_uen",
                      "org_slug", "seed_keys"):
            if field not in d:
                raise Refuse(f"spec missing required field {field!r}")
        key = d["client_key"]
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise Refuse(f"client_key {key!r} is not a 4-16 char lowercase slug")
        alias = d["alias"]
        if not isinstance(alias, str) or not alias:
            raise Refuse("spec alias must be a non-empty string")
        org_uuid = d["org_uuid"]
        if not isinstance(org_uuid, str) or not _UUID_RE.fullmatch(org_uuid):
            raise Refuse(f"org_uuid {org_uuid!r} is not a canonical lowercase UUID")
        if org_uuid[:8] != key[:4].encode().hex():
            raise Refuse(
                f"org_uuid {org_uuid!r} breaks the lookup-free pin: first 8 hex "
                f"chars must be {key[:4]!r} encoded (got {org_uuid[:8]!r}) — the "
                f"convention the live bariq migration documented"
            )
        # the store alias must resolve NOW, at validation time — a spec naming
        # a store that doesn't exist refuses before anything renders
        store_ref(alias)
        seed_keys = tuple(d["seed_keys"])
        if not seed_keys or not all(isinstance(k, str) and k for k in seed_keys):
            raise Refuse("spec seed_keys must be a non-empty list of non-empty strings")
        for label, v in (("org_name", d["org_name"]), ("org_uen", d["org_uen"]),
                         ("org_slug", d["org_slug"])):
            if not isinstance(v, str) or not v.strip():
                raise Refuse(f"spec {label} must be a non-empty string (the org row needs it)")
        return cls(client_key=key, alias=alias, org_uuid=org_uuid,
                   org_name=d["org_name"].strip(), org_uen=d["org_uen"].strip(),
                   org_slug=d["org_slug"].strip(), seed_keys=seed_keys)

    # Derived, never spec-carried: one client's spec cannot name another
    # client's role or tables (the cross-wire class).
    @property
    def editor_role(self) -> str:
        return f"{self.client_key}_editor"

    @property
    def content_table(self) -> str:
        return f"{self.client_key}_site_content"

    @property
    def history_table(self) -> str:
        return f"{self.content_table}_history"


def load_spec(path: Path) -> ClientSpec:
    path = Path(path)
    if not path.is_file():
        raise Refuse(f"spec file not found: {path}")
    try:
        d = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise Refuse(f"spec file {path.name} is not valid JSON: {exc}") from exc
    spec = ClientSpec.from_dict(d)
    if path.stem != spec.client_key:
        raise Refuse(
            f"spec file {path.name} carries client_key {spec.client_key!r} — "
            f"filename must match client_key (a mismatched name is how the "
            f"wrong client's bundle gets rendered)"
        )
    return spec


def spec_path(client_key: str) -> Path:
    return _SPECS_DIR / f"{client_key}.json"
