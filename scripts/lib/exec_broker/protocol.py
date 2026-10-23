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

# §5 error codes — exactly these six, no more (two non-blocking extras were
# explicitly left out of v1 per bus #56789: no dedicated BAD_REQUEST, no
# exec_ledger_id replay check).
class ErrorCode(str, Enum):
    ACL_DENIED = "ACL_DENIED"
    BAD_PROJECT_REF = "BAD_PROJECT_REF"
    GATE_REQUIRED = "GATE_REQUIRED"
    GATE_INVALID = "GATE_INVALID"
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


@dataclass(frozen=True)
class BrokerRequest:
    project_ref: str
    guarded_sql: str
    reason: str
    gate_id: str | None
    caller: str
    exec_ledger_id: str


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
