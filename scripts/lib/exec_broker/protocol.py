#!/usr/bin/env python3
"""protocol.py — hc#95 exec-broker wire protocol: one newline-delimited JSON
request per connection, one newline-delimited JSON response, per the AGREED
socket contract (ihsanos-irsyad main reports/hc95-exec-broker-contract.md,
squash commit 5053826e, §1-3 — FROZEN, build against it verbatim).

Pure, no I/O — safe to unit-test without a socket.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

# §5 error codes — the six v1 codes were frozen at contract commit 5053826e
# per bus #56789 (two non-blocking extras were explicitly left out: no
# dedicated BAD_REQUEST, no exec_ledger_id replay check). READ_ONLY_OVERRIDE
# is the additive seventh, added by the SELECT-only-override-mode follow-up
# (orch-console GO #78974, assignment bus #79024): while the bounded ref
# override is live, a non-plain-read statement is refused with this code.
# Response shape is unchanged; callers must tolerate an unknown code string.
class ErrorCode(str, Enum):
    ACL_DENIED = "ACL_DENIED"
    BAD_PROJECT_REF = "BAD_PROJECT_REF"
    GATE_REQUIRED = "GATE_REQUIRED"
    GATE_INVALID = "GATE_INVALID"
    READ_ONLY_OVERRIDE = "READ_ONLY_OVERRIDE"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class MalformedRequest(ValueError):
    """The request line is not valid JSON, or is missing/mistypes a required
    field. Per bus #56789's non-blocking note 1, this has no dedicated error
    code in v1 — callers map it to INTERNAL_ERROR."""


# §2 request fields, exactly these six keys, all required (gate_id's KEY must
# be present even when its VALUE is null — a missing key means a malformed
# caller, not "no gate needed").
_REQUIRED_KEYS = ("project_ref", "guarded_sql", "reason", "gate_id", "caller", "exec_ledger_id")

# §2 amendment, rollout step (c) (bus #79024/#79465, orch-console-approved
# design bus #79077): migration/pr, UNLIKE gate_id, are OPTIONAL keys — the
# key may be absent from the request entirely, which is treated identically
# to an explicit null. Deliberately different from gate_id's required-but-
# nullable shape: gate_id's absence-vs-null distinguishes "malformed caller"
# from "no gate needed" (a real ambiguity), but migration/pr have no such
# ambiguity to resolve, and requiring the key would force every un-upgraded
# (pre-amendment) caller into MalformedRequest on EVERY call, including
# plain DML, the moment the broker deploys this — before the irsyad-side
# exec_prod change (routed via cc-irsyad-coord) ships. Optional keys mean the
# two sides' deploys are never hard-coupled: an old 6-key caller keeps
# working unchanged (absent -> None -> require_gate's existing unconditional
# DDL refusal, unaffected), and a new caller gets the real migration/PR
# match the moment both sides are up.
_OPTIONAL_KEYS = ("migration", "pr")


@dataclass(frozen=True)
class BrokerRequest:
    project_ref: str
    guarded_sql: str
    reason: str
    gate_id: str | None
    caller: str
    exec_ledger_id: str
    migration: str | None = None
    pr: str | None = None


def parse_request(line: str) -> BrokerRequest:
    """Parse one wire request line into a BrokerRequest. Raises
    MalformedRequest on bad JSON, a non-object top level, a missing required
    key, a non-string value where a string is required, an empty required
    string, or a gate_id that is present but an empty string (gate_id must be
    null or a non-empty string, never "")."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise MalformedRequest(f"invalid JSON: {e}") from e

    if not isinstance(obj, dict):
        raise MalformedRequest(f"request must be a JSON object, got {type(obj).__name__}")

    for key in _REQUIRED_KEYS:
        if key not in obj:
            raise MalformedRequest(f"missing required field {key!r}")

    gate_id = obj["gate_id"]
    if gate_id is not None and not (isinstance(gate_id, str) and gate_id):
        raise MalformedRequest("gate_id must be null or a non-empty string")

    optional_fields: dict[str, "str | None"] = {}
    for key in _OPTIONAL_KEYS:
        value = obj.get(key)
        if value is not None and not (isinstance(value, str) and value):
            raise MalformedRequest(f"{key!r} must be absent, null, or a non-empty string")
        optional_fields[key] = value

    string_fields: dict[str, str] = {}
    for key in ("project_ref", "guarded_sql", "caller", "exec_ledger_id"):
        value = obj[key]
        if not isinstance(value, str) or not value:
            raise MalformedRequest(f"{key!r} must be a non-empty string")
        string_fields[key] = value

    reason = obj["reason"]
    if not isinstance(reason, str):
        raise MalformedRequest("'reason' must be a string")

    return BrokerRequest(
        project_ref=string_fields["project_ref"],
        guarded_sql=string_fields["guarded_sql"],
        reason=reason,
        gate_id=gate_id,
        caller=string_fields["caller"],
        exec_ledger_id=string_fields["exec_ledger_id"],
        migration=optional_fields["migration"],
        pr=optional_fields["pr"],
    )


def encode_ok(rows: list) -> bytes:
    """§3 success response — newline-delimited JSON."""
    return json.dumps({"status": "ok", "rows": rows}, sort_keys=True).encode("utf-8") + b"\n"


def encode_error(code: ErrorCode, message: str) -> bytes:
    """§3 failure response — exactly one code per error, never echoes a raw
    upstream connection error (host/port) per the contract's leak discipline."""
    return json.dumps(
        {"status": "error", "error": {"code": code.value, "message": message}},
        sort_keys=True,
    ).encode("utf-8") + b"\n"
