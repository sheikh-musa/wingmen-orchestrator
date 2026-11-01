#!/usr/bin/env python3
"""vault_log.py — append-only exec_broker_access_log writer (contract §6).

Two writes per call, always to the SAME row: log_started() before the
mgmt-API call (status=started), log_finalized() after (status=ok/error).
Never writes an explicit id — the identity column default assigns it, read
back via RETURNING id (standing fleet rule: never hand-write a serial/
identity id, see held_commitments #418/#419).

Table name is exec_broker_access_log, NOT vault_access_log -- that name
collides with the live fleet-wide secrets-access audit table (migrations/
065_secrets_vault.sql, 11013 rows, ~12 agents writing continuously),
caught by orch-console before anything was applied (bus #72279). Never
rename this back to vault_access_log.
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
    # B2/--ref (bus #89814 §3.5): the gate's ref anchor, recorded so a
    # ref-only approval stays auditable after the fact. None for every caller
    # that does not send `ref` -- and then the INSERT below is byte-identical
    # to the pre-amendment one (no gate_ref column referenced), so the
    # gate_ref column (migrations/142_hc95_exec_broker_gate_ref.sql) only
    # has to exist before the first ref-carrying call. Until it is applied, a
    # ref-carrying call fails at this INSERT -> INTERNAL_ERROR, before any
    # validation or mgmt-API call: fail closed, nothing executes.
    gate_ref: str | None = None
    # Queue row 272: the daemon boot that wrote this row (the same uuid the
    # heartbeat uses), so a restart can tell ITS OWN in-flight rows from a dead
    # previous boot's exactly. Column: migrations/147_hc95_exec_broker_in_doubt.sql.
    # None -> not referenced (statement identical to before), like gate_ref.
    boot_id: str | None = None


# Queue row 272 (cc-fleet-health bus #89969): the status a 'started' row from a
# PREVIOUS daemon boot is moved to at boot. Fleet-health keys on this literal.
IN_DOUBT_STATUS = "in_doubt"

# Marks every still-'started' row (ts_end IS NULL) that is NOT this boot's, and
# NOT owned by a broker that is provably alive on ANOTHER host right now (a fresh
# exec_broker_heartbeat row held by a different holder_host): its execution
# outcome is unknown (SIGKILL/OOM mid upstream call). Rows written before
# boot_id existed (boot_id IS NULL) are by definition from a previous boot.
# Sets status + in_doubt_at ONLY: ts_end stays NULL (we do not know when, or
# whether, the upstream call finished), and nothing is retried or finalized as
# ok/error. Count + oldest age come back for the boot log (no SQL text, no PII).
MARK_IN_DOUBT_SQL = (
    "WITH upd AS ("
    "  UPDATE exec_broker_access_log SET status = 'in_doubt', in_doubt_at = now() "
    "  WHERE status = 'started' AND ts_end IS NULL "
    "    AND boot_id IS DISTINCT FROM %(boot)s::uuid "
    "    AND (boot_id IS NULL OR boot_id NOT IN ("
    "      SELECT hb.boot_id FROM exec_broker_heartbeat hb "
    "      WHERE hb.holder_host <> %(host)s "
    "        AND hb.beat_at + make_interval(secs => hb.ttl_seconds) >= now())) "
    "  RETURNING ts_start"
    ") SELECT count(*) AS n, "
    "  COALESCE(EXTRACT(EPOCH FROM now() - min(ts_start)), 0)::bigint AS oldest_age_s FROM upd"
)


def mark_previous_boots_in_doubt(cur, *, boot_id: str, host: str) -> "tuple[int, int]":
    """Queue row 272: (rows marked in_doubt, oldest marked row's age in
    seconds). Never re-executes anything and never sets ts_end."""
    cur.execute(MARK_IN_DOUBT_SQL, {"boot": boot_id, "host": host})
    row = cur.fetchone()
    if isinstance(row, dict):
        return int(row["n"]), int(row["oldest_age_s"])
    return int(row[0]), int(row[1])


def log_started(cur, entry: VaultLogEntry) -> int:
    """INSERT one row, status='started'. Returns the new row's id (never
    computed locally — read back via RETURNING id)."""
    params = (
        entry.exec_ledger_id,
        entry.caller,
        entry.project_ref,
        hashlib.sha256(entry.guarded_sql.encode("utf-8")).hexdigest(),
        entry.reason,
        entry.gate_id,
    )
    cols = ["exec_ledger_id", "caller", "project_ref", "sql_sha256", "reason", "gate_id"]
    if entry.gate_ref is not None:
        cols.append("gate_ref")
        params += (entry.gate_ref,)
    if entry.boot_id is not None:
        cols.append("boot_id")
        params += (entry.boot_id,)
    cur.execute(
        "INSERT INTO exec_broker_access_log "
        f"({', '.join(cols)}, status, ts_start) "
        f"VALUES ({', '.join(['%s'] * len(cols))}, 'started', now()) RETURNING id",
        params,
    )
    return cur.fetchone()["id"]


def record_executed(cur, row_id: int, executed_prologue: str, executed_sql_sha256: str) -> None:
    """Amended ruling C (bus #89846): when the broker prepends the read-only
    prologue to a gate-free read, the bytes it EXECUTES differ from the bytes
    it RECEIVED. sql_sha256 stays the hash of the received bytes (the caller
    binding, unchanged); this records what actually ran, so the audit never
    implies it executed bytes it did not. Both columns stay NULL when the
    received bytes were executed unchanged (every gated statement).
    Columns: migrations/142_hc95_exec_broker_gate_ref.sql."""
    cur.execute(
        "UPDATE exec_broker_access_log SET executed_prologue = %s, executed_sql_sha256 = %s WHERE id = %s",
        (executed_prologue, executed_sql_sha256, row_id),
    )


def log_finalized(
    cur, row_id: int, status: str, error_code: str | None, error_reason: "str | None" = None,
) -> None:
    """UPDATE the SAME row started by log_started — never a second INSERT.
    status is 'ok' or 'error'. error_reason (B3, console #89841) is the
    optional machine-readable reason token; without it the statement is
    byte-identical to the pre-B3 one (no error_reason column referenced)."""
    if error_reason is None:
        cur.execute(
            "UPDATE exec_broker_access_log SET status = %s, error_code = %s, ts_end = now() WHERE id = %s",
            (status, error_code, row_id),
        )
    else:
        cur.execute(
            "UPDATE exec_broker_access_log SET status = %s, error_code = %s, error_reason = %s, "
            "ts_end = now() WHERE id = %s",
            (status, error_code, error_reason, row_id),
        )


# B3 single-use (bus #89814 §4.2-4.3, CONTRACT AMENDMENT -- see protocol.py):
# the FIRST execution whose gate fully validates consumes that gate_id, bound
# to this row's exec_ledger_id. Enforced by the DATABASE, not by a
# check-then-write in Python: migrations/143_hc95_exec_broker_gate_single_use.sql
# adds gate_consumed + a partial UNIQUE index on (gate_id) WHERE gate_consumed,
# so a second row (any other exec_ledger_id, or a concurrent duplicate) trying
# to consume the same gate_id raises unique_violation (SQLSTATE 23505) -- a
# concurrent duplicate blocks on the index until the first commits, then fails
# closed. server.py maps 23505 to GATE_INVALID; any other error (e.g. the column
# not yet existing) to INTERNAL_ERROR. Either way, BEFORE the mgmt-API call.
UNIQUE_VIOLATION_SQLSTATE = "23505"


def consume_gate(cur, row_id: int) -> None:
    """Mark THIS row (written by log_started, carrying gate_id) as the one
    execution that consumed its gate. Raises the DB's unique_violation if the
    gate_id was already consumed by another row."""
    cur.execute(
        "UPDATE exec_broker_access_log SET gate_consumed = true "
        "WHERE id = %s AND gate_id IS NOT NULL AND gate_consumed = false",
        (row_id,),
    )
    if getattr(cur, "rowcount", 1) != 1:
        # The row we just wrote must exist, carry a gate_id and be unconsumed;
        # anything else is a bug -- refuse rather than execute unbound.
        raise RuntimeError(f"consume_gate: exec_broker_access_log row {row_id} not consumable")
