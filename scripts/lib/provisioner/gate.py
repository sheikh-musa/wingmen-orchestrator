"""gate.py — a provision verb runs only against a real console/cai GO, and
the GO is bound to the EXACT artifact (plan §4/§9).

Mirrors exec_broker's require_gate row read (same agents, same types, same
is_past re-check, same whole-word silo mention — vendored logic, see
scripts/lib/exec_broker/gate_validation.py) and STRENGTHENS the text-match:
the gate row must mention the store ref (or its registry alias), the
client_key, AND the rendered bundle's sha256. The sha bind is the anti-drift
control exec_broker milestone (a) couldn't have: a GO written before a spec
or template change no longer matches the re-rendered artifact, because the
bytes are different — the refusal happens before any connection to the store.

One-artifact-per-gate (a second application citing the same GO) is enforced
by the access ledger at execution time, not here — this module only sees the
bus row.
"""
from __future__ import annotations

import re

from .registry import Refuse
from .render import Bundle
from .spec import ClientSpec

_GATE_AGENTS = {"orch-console", "cai"}
_GATE_TYPES = {"decision", "agreed"}


def _mentions_word(text: str, needle: str) -> bool:
    """Whole-word match (cc-quality #56681 finding 2, vendored): a short
    needle must not match merely because it appears inside an unrelated word
    — 'acme' inside 'acmeCLIENT' or a 20-char ref inside a longer token."""
    return re.search(rf"\b{re.escape(needle.lower())}\b", text.lower()) is not None


def _mentions_store(text: str, silo_ref: str, alias: str) -> bool:
    """The raw ref or its registry alias — the same two needles exec_broker's
    _text_mentions_silo derives, sourced here from the spec + the bundle."""
    return _mentions_word(text, silo_ref) or _mentions_word(text, alias)


def require_provision_gate(
    gate_id: str | None,
    *,
    spec: ClientSpec,
    bundle: Bundle,
    cur,
) -> None:
    """Re-fetches gate_id from the fleet bus via `cur` (a psycopg cursor
    opened with row_factory=psycopg.rows.dict_row) and validates the GO
    binds THIS client (spec), THIS store (bundle.silo_ref, registry-loaded
    at render time), and THESE artifact bytes (bundle.sha256). Raises Refuse
    on any failure, before any store connection is opened."""
    if not gate_id or not str(gate_id).isdigit():
        raise Refuse(f"gate_id {gate_id!r} must be a positive integer agent_messages id")

    cur.execute(
        "SELECT from_agent, message_type, subject, body, (created_at < now()) AS is_past "
        "FROM agent_messages WHERE id = %s",
        (int(gate_id),),
    )
    row = cur.fetchone()

    if row is None:
        raise Refuse(f"gate {gate_id} not found on the fleet bus")
    if row["from_agent"] not in _GATE_AGENTS:
        raise Refuse(
            f"gate {gate_id} is from {row['from_agent']!r}, not one of {sorted(_GATE_AGENTS)}"
        )
    if row["message_type"] not in _GATE_TYPES:
        raise Refuse(
            f"gate {gate_id} has message_type {row['message_type']!r}, "
            f"not one of {sorted(_GATE_TYPES)}"
        )
    if not row["is_past"]:
        raise Refuse(f"gate {gate_id} has created_at not in the past")

    text = f"{row.get('subject') or ''} {row.get('body') or ''}"
    if not _mentions_store(text, bundle.silo_ref, spec.alias):
        raise Refuse(
            f"gate {gate_id} does not mention the target store "
            f"{bundle.silo_ref!r} (or its alias {spec.alias!r})"
        )
    if not _mentions_word(text, spec.client_key):
        raise Refuse(
            f"gate {gate_id} does not mention client_key {spec.client_key!r} — "
            "a GO for a different client on the same store authorizes nothing here"
        )
    if bundle.sha256.lower() not in text.lower():
        raise Refuse(
            f"gate {gate_id} does not mention this bundle's sha256 "
            f"{bundle.sha256[:16]}… — a GO written before a spec or template "
            "change no longer matches the rendered artifact (re-approve, or "
            "revert the change)"
        )
