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
# GATE_MISMATCH is the additive eighth, added by Part A rollout step (c)'s
# byte-binding check for lexically hazardous SQL (orch-console ruling bus
# #79477): the executed bytes' sha12 does not equal the sha12 the gate cites,
# so the gate does not bind to these exact bytes. Response shape is unchanged;
# callers must tolerate an unknown code string.
#
# CONTRACT AMENDMENT B3 (cc-fleet-health cutover spec bus #89814 §4,
# 2026-11-01) -- SUPERSEDES the "no exec_ledger_id replay check" omission
# recorded above, deliberately and explicitly, not as a side effect of B1:
#   (1) GATE FRESHNESS: a gate older than the broker's configured max age
#       (EXEC_BROKER_GATE_MAX_AGE_SECONDS, default 24h, hard-bounded in code
#       to 1s..72h) -> GATE_INVALID.
#   (2) SINGLE-USE: the first execution whose gate fully validates consumes
#       it, bound to that request's exec_ledger_id; any later request citing
#       the same gate_id (another exec_ledger_id, a retry of the same one, or
#       a concurrent duplicate) -> GATE_INVALID. Enforced by a partial UNIQUE
#       index in exec_broker_access_log (DB-side, fails closed under
#       concurrency), never a Python check-then-write.
#   (3) gate_id must be canonical digits (no leading zeros) -> else GATE_INVALID.
#   (4) CUTOVER FLOOR (console #89841 ruling e): a gate created before
#       EXEC_BROKER_GATE_NOT_BEFORE -> GATE_INVALID; with no floor configured,
#       gates on every non-QA ref are refused.
#   (5) REASON TOKENS: GATE_INVALID refusals from (1)/(2)/(4) carry a distinct
#       machine-readable `reason` (gate_too_old, gate_already_consumed,
#       gate_before_cutover, gate_cutover_unset, gate_config_invalid) in the
#       response error object and in exec_broker_access_log.error_reason, so a
#       replay is distinguishable from a malformed gate.
# Both reuse the existing GATE_INVALID code -- no new code is added to the
# frozen set, and the response shape is unchanged.
class ErrorCode(str, Enum):
    ACL_DENIED = "ACL_DENIED"
    BAD_PROJECT_REF = "BAD_PROJECT_REF"
    GATE_REQUIRED = "GATE_REQUIRED"
    GATE_INVALID = "GATE_INVALID"
    GATE_MISMATCH = "GATE_MISMATCH"
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
#
# §2 amendment B2 (cc-fleet-health cutover spec bus #89814 §2, 2026-11-01):
# `ref` joins migration/pr as a third OPTIONAL key, for the same
# deploy-decoupling reason. It is the --ref gate anchor (op#N/hc#N/q#N/cp#N)
# exec_prod already accepts on the direct path (console #83951); its SHAPE is
# validated in gate_validation.require_gate (GATE_INVALID), not here, so a
# malformed ref is still audited to exec_broker_access_log before refusal.
# Contract change, recorded with the commit that lands it: a new OPTIONAL
# request key; no new error code; response shape unchanged. An old caller
# that omits it is unaffected.
_OPTIONAL_KEYS = ("migration", "pr", "ref")


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
    ref: str | None = None


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
        ref=optional_fields["ref"],
    )


def encode_ok(rows: list) -> bytes:
    """§3 success response — newline-delimited JSON."""
    return json.dumps({"status": "ok", "rows": rows}, sort_keys=True).encode("utf-8") + b"\n"


def encode_error(code: ErrorCode, message: str, reason: "str | None" = None) -> bytes:
    """§3 failure response — exactly one code per error, never echoes a raw
    upstream connection error (host/port) per the contract's leak discipline.

    B3 amendment (console #89841): an optional machine-readable `reason`
    token (e.g. gate_already_consumed) is added to the error object ONLY when
    present, and the message is prefixed with it. Without a reason the bytes
    are identical to the v1 response; callers must tolerate the extra key."""
    error = {"code": code.value, "message": message}
    if reason is not None:
        error["reason"] = reason
        error["message"] = f"{reason}: {message}"
    return json.dumps({"status": "error", "error": error}, sort_keys=True).encode("utf-8") + b"\n"
