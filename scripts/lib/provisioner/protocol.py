"""protocol.py — provisioner wire protocol: one newline-JSON request per
connection, one newline-JSON response (mirrors exec_broker's frozen §1-§3
shape, adapted per plan §9).

Five request fields, all required. gate_id is REQUIRED and non-null — both
verbs are DDL, there is no gateless path (unlike exec_broker, whose DML-only
requests pass gateless).

Pure, no I/O — safe to unit-test without a socket.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    """Exactly six, mirroring exec_broker's discipline of a small closed
    set. BAD_VERB takes the place of BAD_PROJECT_REF (there is no
    caller-supplied ref on this broker — the store is fixed by the registry
    and the client spec); SPEC_INVALID covers unloadable/refused client
    specs; the server-major fail-closed floor reports UPSTREAM_ERROR with a
    precise message (it is a property of the store, not of the request)."""

    ACL_DENIED = "ACL_DENIED"
    BAD_VERB = "BAD_VERB"
    SPEC_INVALID = "SPEC_INVALID"
    GATE_INVALID = "GATE_INVALID"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class MalformedRequest(ValueError):
    """The request line is not valid JSON or mistypes a required field.
    Same v1 convention as exec_broker (bus #56789 note 1): no dedicated
    code — callers map it to INTERNAL_ERROR."""


_REQUIRED_KEYS = ("verb", "client_key", "gate_id", "reason", "caller")

# gate ids are agent_messages ids: digits only
_GATE_ID_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class ProvisionerRequest:
    verb: str          # 'provision' | 'revert' — membership checked server-side
    client_key: str
    gate_id: str
    reason: str
    caller: str


def parse_request(line: str) -> ProvisionerRequest:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise MalformedRequest(f"invalid JSON: {e}") from e

    if not isinstance(obj, dict):
        raise MalformedRequest(f"request must be a JSON object, got {type(obj).__name__}")

    for key in _REQUIRED_KEYS:
        if key not in obj:
            raise MalformedRequest(f"missing required field {key!r}")

    string_fields: dict[str, str] = {}
    for key in ("verb", "client_key", "caller"):
        value = obj[key]
        if not isinstance(value, str) or not value:
            raise MalformedRequest(f"{key!r} must be a non-empty string")
        string_fields[key] = value

    gate_id = obj["gate_id"]
    if not isinstance(gate_id, str) or not _GATE_ID_RE.fullmatch(gate_id):
        raise MalformedRequest(
            "gate_id must be a non-empty numeric agent_messages id (both verbs are DDL)"
        )

    reason = obj["reason"]
    if not isinstance(reason, str):
        raise MalformedRequest("'reason' must be a string")

    return ProvisionerRequest(
        verb=string_fields["verb"],
        client_key=string_fields["client_key"],
        gate_id=gate_id,
        reason=reason,
        caller=string_fields["caller"],
    )


def encode_ok(*, client_key: str, verb: str, bundle_sha256: str) -> bytes:
    """Success response — newline-delimited JSON carrying the artifact
    identity so the caller can pin what actually ran."""
    return json.dumps(
        {"status": "ok", "client_key": client_key, "verb": verb, "bundle_sha256": bundle_sha256},
        sort_keys=True,
    ).encode("utf-8") + b"\n"


_DSN_RE = re.compile(r"[a-z0-9_+]+://\S+")


def encode_error(code: ErrorCode, message: str) -> bytes:
    """Failure response — exactly one code per error. Never echoes a raw
    upstream connection error (DSN-shaped text) per the contract's leak
    discipline: anything scheme://… is stripped before the message goes on
    the wire."""
    sanitized = _DSN_RE.sub("[redacted-dsn]", message)
    return json.dumps(
        {"status": "error", "error": {"code": code.value, "message": sanitized}},
        sort_keys=True,
    ).encode("utf-8") + b"\n"
