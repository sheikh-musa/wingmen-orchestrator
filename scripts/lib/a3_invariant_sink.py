#!/usr/bin/env python3
"""a3_invariant_sink — the substrate write half of the CAI-985 A3 automation.

The A3 isolation check runs against the ceayj tenant (under orch-console, the credential-holder —
CAI-981 'move the work to the credential', Nazim #24061). Its RESULT must land where the auditor
(cc-quality) can read it from the SUBSTRATE side, else the re-audit returns could_not_verify
forever (CAI-1000 D7 / cai Doctrine-1). This module is that landing.

ENCODING — cai CAI-RESP-1014 (she stewards invariant_registry; this is her ruling, not my design):
  PASS  -> invariant_registry.gate_status='COVERED', last_asserted_at=now()  (the measurer ran AND
           the invariant held; the existing 047 view then reads RESIDENCY-1 EXERCISED).
  FAIL  -> gate_status='pending', last_asserted_at UNCHANGED (a run that did NOT prove the invariant
           true must let the row go stale; 'pending' reads NOT-EXERCISED in the 047 view — so a live
           leak can never read green, BY CONSTRUCTION, with no CHECK/view change).
  ERROR -> gate_status='pending' too (D6 fail-closed / D5 'a green that means I was not allowed to
           look'): the measurer ran but could not complete. Distinct from a leak-FAIL so the run
           record separates 'leak found' from 'could not measure'. (Proposed to cai #24064 as a
           refinement of her PASS/FAIL; if she rules two-state, map ERROR->FAIL here.)

Every run ALSO appends one immutable row to invariant_assertion_runs (the joinable, substrate-
readable verdict + evidence + run_at proof-of-run): a reader of gate_status is ONE JOIN from the
reason, and 'pending-failed' is distinguishable from 'pending-never-ran'. (That table is created by
migrations/drafts/invariant_assertion_runs.sql, pending cai review + §6.6 grant.)

BOUNDARY (made REAL here, cai Q4): assert_ops_only runs BEFORE any write and gates BOTH tables.
Neither is in GOVERNANCE_TABLES, so the SRE measured-evidence write is permitted (CAI-RESP-986 §3:
the measurer writes its measurement) — but the guard was previously UNCALLED anywhere, so wiring it
here is what makes it a precondition rather than documentation. If either table is ever reclassified
governance, this fail-closes loudly instead of silently overreaching.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

from scripts.lib.fleet_health_boundaries import assert_ops_only, BoundaryViolation, SRE_AGENT_ID

__all__ = ["record_a3_run", "BoundaryViolation", "PASS", "FAIL", "ERROR"]

RESIDENCY_INVARIANT_REF = "RESIDENCY-1"
RUNS_TABLE = "invariant_assertion_runs"

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"
_GATE_ON = {PASS: "COVERED", FAIL: "pending", ERROR: "pending"}


def _check_outcome_count_agree(outcome: str, finding_count: int) -> None:
    """Mirror the DB CHECK (invariant_assertion_runs_outcome_count_agree) fail-loud in-process, so
    a contradiction crashes at the call site with a clear message, not as a raw constraint error:
    PASS is clean (0), FAIL carries findings (>0), ERROR found nothing because it could not look (0)."""
    if outcome == PASS and finding_count != 0:
        raise ValueError(f"PASS must carry 0 findings, got {finding_count}")
    if outcome == FAIL and finding_count <= 0:
        raise ValueError(f"FAIL must carry >0 findings, got {finding_count}")
    if outcome == ERROR and finding_count != 0:
        raise ValueError(f"ERROR must carry 0 findings, got {finding_count}")


def record_a3_run(cur, *, outcome: str, scope_checked: str, finding_count: int,
                  evidence, invariant_ref: str = RESIDENCY_INVARIANT_REF,
                  run_by: str = SRE_AGENT_ID, identity: str | None = SRE_AGENT_ID) -> str:
    """Record one A3 measurer run per CAI-RESP-1014 (see module docstring for the encoding).

    Writes, in the caller's transaction (so both land atomically, or a test rolls back):
      1. invariant_registry.gate_status (+ last_asserted_at on PASS only), and
      2. one immutable invariant_assertion_runs row (outcome, scope, finding_count, evidence, run_by).
    assert_ops_only gates BOTH tables first (fail-closed). Returns the gate_status written.

    Does NOT commit — the caller owns the txn."""
    if outcome not in _GATE_ON:
        raise ValueError(f"outcome must be one of {sorted(_GATE_ON)}, got {outcome!r}")
    _check_outcome_count_agree(outcome, finding_count)

    # Boundary FIRST, both tables — a raise here aborts before any write.
    assert_ops_only("invariant_registry", identity)
    assert_ops_only(RUNS_TABLE, identity)

    gate = _GATE_ON[outcome]
    if outcome == PASS:
        cur.execute(
            "UPDATE invariant_registry SET gate_status=%s, last_asserted_at=now(), updated_at=now() "
            "WHERE invariant_ref=%s",
            (gate, invariant_ref))
    else:
        # FAIL / ERROR: not proven true -> pending, and DO NOT touch last_asserted_at (let it go stale).
        cur.execute(
            "UPDATE invariant_registry SET gate_status=%s, updated_at=now() "
            "WHERE invariant_ref=%s",
            (gate, invariant_ref))

    cur.execute(
        "INSERT INTO invariant_assertion_runs "
        "(invariant_ref, outcome, scope_checked, finding_count, evidence, run_by) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (invariant_ref, outcome, scope_checked, finding_count,
         Jsonb(evidence) if evidence is not None else None, run_by))
    return gate
