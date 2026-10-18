#!/usr/bin/env python3
"""vault_log.py — append-only vault_access_log writer (contract §6).

Two writes per call, always to the SAME row: log_started() before the
mgmt-API call (status=started), log_finalized() after (status=ok/error).
Never writes an explicit id — the identity column default assigns it, read
back via RETURNING id (standing fleet rule: never hand-write a serial/
identity id, see held_commitments #418/#419).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class VaultLogEntry:
    exec_ledger_id: str
    caller: str
    project_ref: str
    guarded_sql: str
    reason: str
    gate_id: str | None


def log_started(cur, entry: VaultLogEntry) -> int:
    """INSERT one row, status='started'. Returns the new row's id (never
    computed locally — read back via RETURNING id)."""
    cur.execute(
        "INSERT INTO vault_access_log "
        "(exec_ledger_id, caller, project_ref, sql_sha256, reason, gate_id, status, ts_start) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'started', now()) RETURNING id",
        (
            entry.exec_ledger_id,
            entry.caller,
            entry.project_ref,
            hashlib.sha256(entry.guarded_sql.encode("utf-8")).hexdigest(),
            entry.reason,
            entry.gate_id,
        ),
    )
    return cur.fetchone()["id"]


def log_finalized(cur, row_id: int, status: str, error_code: str | None) -> None:
    """UPDATE the SAME row started by log_started — never a second INSERT.
    status is 'ok' or 'error'."""
    cur.execute(
        "UPDATE vault_access_log SET status = %s, error_code = %s, ts_end = now() WHERE id = %s",
        (status, error_code, row_id),
    )
