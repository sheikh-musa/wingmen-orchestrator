"""CADENCE-008 A execution-grant predicate (cai #2066, conservative v1).

Verdict over a strategic_decisions row. Anything not GRANTED is report-only;
ambiguity in the caller => hard stop + escalate (handled in main cycle).
The grant SIGNAL is execution_status == GRANT_SIGNAL; pending cai ratification
of #2066 the cycle keeps the execute arm unwired regardless of this verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

GRANT_SIGNAL = "granted"  # set ONLY by the ruling author (cai)
EXECUTOR_REPO = "ihsanos"
# A window is CLOSED (executable) only for affirmatively-closed states. Allowlist,
# not blocklist: an actively 'challenged' / 'superseded' / still-open ruling must
# never execute, and the timer-flip process guarantees a closed window leaves
# 'challenge_window' (verified: zero stale-open rows in prod).
CLOSED_CHALLENGE_STATES = frozenset({"accepted", "accepted_by_timeout", "unchallenged"})
GRANTED = "granted"
REPORT_ONLY = "report_only"
REFUSED_MIGRATION = "refused_migration"


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str


def evaluate_grant(
    row: dict, *, is_migration: bool, migration_filename: Optional[str]
) -> Verdict:
    if row.get("execution_status") != GRANT_SIGNAL:
        return Verdict(REPORT_ONLY, "execution_status is not 'granted'")
    repos = row.get("repos_affected") or []
    if EXECUTOR_REPO not in repos:
        return Verdict(REPORT_ONLY, "ruling does not name ihsanos as executor")
    if row.get("challenge_status") not in CLOSED_CHALLENGE_STATES:
        return Verdict(REPORT_ONLY, "challenge window not affirmatively closed")
    if is_migration:
        decision_text = row.get("decision") or ""
        if not migration_filename or migration_filename not in decision_text:
            return Verdict(
                REFUSED_MIGRATION,
                "migration filename not literally named in ruling",
            )
    return Verdict(GRANTED, "all grant conditions satisfied")
