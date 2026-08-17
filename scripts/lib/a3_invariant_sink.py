#!/usr/bin/env python3
"""a3_invariant_sink — the substrate write half of the CAI-985 A3 automation.

The A3 isolation check runs against the ceayj tenant (under orch-console, the credential-holder —
CAI-981 'move the work to the credential', Nazim #24061). Its RESULT must land somewhere the
auditor (cc-quality) can read from the SUBSTRATE side, else the re-audit returns could_not_verify
forever (D7 / cai Doctrine 1). This module is that landing: it records, on RESIDENCY-1 in
invariant_registry, that the measurer is LIVE and freshly-run — gate_status='COVERED' +
last_asserted_at=now(). invariant_registry_state (mig 047) then reads RESIDENCY-1 as EXERCISED
(it requires gate_status='COVERED' AND last_asserted_at < 30d, gate_status dominating).

SCOPE — FORK-INDEPENDENT CORE ONLY (Nazim #24061 'start the sink now'):
  * gate_status='COVERED' answers "is there a LIVE MEASURER" — NOT the invariant's PASS/FAIL.
  * The PASS/FAIL verdict lives in a separate, substrate-readable, JOINABLE run record whose
    encoding cai OWNS (FORK 2, consulted #24064). This module does NOT write that yet, and does
    NOT implement the fail path, until cai rules. Writing COVERED on a failing invariant without
    a joinable verdict would be a false green by omission — the exact thing invariant_registry
    exists to prevent (Nazim's condition #24061).

BOUNDARY (made REAL here): assert_ops_only runs BEFORE the write. invariant_registry is not in
GOVERNANCE_TABLES, so the SRE measured-evidence write is permitted (CAI-RESP-986 §3), but the
guard was previously UNCALLED anywhere — wiring it here makes it a genuine precondition, not
documentation (Nazim #24061, on record). If invariant_registry is ever reclassified governance,
this write fail-closes loudly instead of silently overreaching.
"""
from __future__ import annotations

from scripts.lib.fleet_health_boundaries import assert_ops_only, BoundaryViolation, SRE_AGENT_ID

__all__ = ["record_measurer_live", "BoundaryViolation"]

RESIDENCY_INVARIANT_REF = "RESIDENCY-1"


def record_measurer_live(cur, invariant_ref: str = RESIDENCY_INVARIANT_REF,
                         identity: str | None = SRE_AGENT_ID) -> int:
    """Record that the A3 measurer is LIVE and freshly-run against `invariant_ref`:
    gate_status='COVERED' (CHECK-legal; 'MEASURED' is CHECK-illegal) + last_asserted_at=now().

    The ops/governance boundary is asserted FIRST and gates the write — a BoundaryViolation
    aborts before any UPDATE. Does NOT commit: the caller owns the transaction (so a run that
    also writes the joinable verdict record, once cai rules on FORK 2, commits both atomically,
    and tests can roll back). Returns the number of rows updated (1 if the invariant existed).

    NB: this records only that the measurer RAN. It does not — must not — assert the invariant
    PASSED; that verdict is the pending cai-owned joinable run record (FORK 2)."""
    assert_ops_only("invariant_registry", identity)
    cur.execute(
        "UPDATE invariant_registry "
        "SET gate_status='COVERED', last_asserted_at=now(), updated_at=now() "
        "WHERE invariant_ref=%s",
        (invariant_ref,),
    )
    return cur.rowcount
