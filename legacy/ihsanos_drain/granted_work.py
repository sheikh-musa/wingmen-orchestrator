"""CADENCE-008 A: resolve which granted rulings the drain worker WOULD execute.

The unit of authority is a strategic_decisions row with execution_status='granted'
(cai #2066). This module fetches candidates and classifies each via the ratified
grant predicate. The report-only cycle uses this to emit "would-execute" reports
without acting; the execute arm (plan Task 7) consumes the executable list.
"""
from __future__ import annotations

from ihsanos_drain.grant import GRANT_SIGNAL, GRANTED, evaluate_grant


def candidate_query() -> tuple[str, tuple]:
    """Granted-signal rulings; challenge state + executor checked in summarize()."""
    sql = (
        "SELECT decision_ref, execution_status, repos_affected, challenge_status, "
        "decision FROM strategic_decisions WHERE execution_status = %s "
        "ORDER BY decided_at ASC"
    )
    return sql, (GRANT_SIGNAL,)


def summarize(rows) -> dict:
    """Split candidates into executable-now vs held (with per-ruling reasons).

    Report pass evaluates the non-migration predicate; the migration sub-rule
    binds at execute time when an actual migration file is in play.
    """
    executable: list[str] = []
    held: list[tuple[str, str]] = []
    for row in rows:
        ref = row.get("decision_ref")
        verdict = evaluate_grant(row, is_migration=False, migration_filename=None)
        if verdict.status == GRANTED:
            executable.append(ref)
        else:
            held.append((ref, verdict.reason))
    return {"executable": executable, "held": held}
