"""ledger.py — substrate-side access ledger for the tenant provisioner
(plan §6/§9).

Mirrors exec_broker's vault_log two-phase SAME-ROW pattern: log_started()
INSERTs one row (status='started') and reads the id back via RETURNING
(standing fleet rule: never hand-write a serial/identity id, see
held_commitments #418/#419); log_finalized() UPDATEs that same row — never a
second INSERT. A row stuck at 'started' past a threshold is itself a signal
(provisioner crashed mid-apply) — reconciliation is the operator's, same
posture as exec_broker's dead-man.

One-artifact-per-gate (the §4 strengthening's spend rule): gate_already_used()
reports True when ANY row for this gate+verb is 'started' or 'ok' — the gate
is spent. Only attempts that ended in 'error' leave it retryable. The
migration's partial unique index on (gate_id, verb) WHERE status = 'ok'
enforces the success half at the DB level, belt-and-braces under this check.
"""
from __future__ import annotations

from dataclasses import dataclass

from .registry import Refuse

_FINAL_STATUSES = ("ok", "error")


@dataclass(frozen=True)
class AccessLogEntry:
    verb: str                 # 'provision' | 'revert'
    client_key: str
    silo_ref: str             # registry-loaded, carried by the Bundle
    bundle_sha256: str
    gate_id: str
    peer_uid: str | None      # AF_UNIX caller identity, when known
    reason: str = ""


def log_started(cur, entry: AccessLogEntry) -> int:
    """INSERT one row, status='started'. Returns the new row's id (never
    computed locally — read back via RETURNING id)."""
    cur.execute(
        "INSERT INTO provisioner_access_log "
        "(verb, client_key, silo_ref, bundle_sha256, gate_id, peer_uid, reason, status, ts_start) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'started', now()) RETURNING id",
        (
            entry.verb,
            entry.client_key,
            entry.silo_ref,
            entry.bundle_sha256,
            entry.gate_id,
            entry.peer_uid,
            entry.reason,
        ),
    )
    return cur.fetchone()["id"]


def log_finalized(cur, row_id: int, status: str, error_code: str | None = None) -> None:
    """UPDATE the SAME row started by log_started — never a second INSERT.
    status is 'ok' or 'error'."""
    if status not in _FINAL_STATUSES:
        raise Refuse(f"finalize status must be one of {_FINAL_STATUSES}, got {status!r}")
    cur.execute(
        "UPDATE provisioner_access_log SET status = %s, error_code = %s, ts_end = now() WHERE id = %s",
        (status, error_code, row_id),
    )


def gate_already_used(cur, gate_id: str, verb: str) -> bool:
    """One-artifact-per-gate: True when any row for this gate+verb is
    'started' (in-flight, or crashed mid-apply — operator reconciles) or
    'ok' (spent). Attempts that ended in 'error' do not spend the gate."""
    cur.execute(
        "SELECT 1 FROM provisioner_access_log "
        "WHERE gate_id = %s AND verb = %s AND status IN ('started', 'ok') LIMIT 1",
        (gate_id, verb),
    )
    return cur.fetchone() is not None
