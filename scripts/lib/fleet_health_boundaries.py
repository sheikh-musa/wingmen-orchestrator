#!/usr/bin/env python3
"""fleet_health_boundaries.py — CAI-RESP-501 HARD boundaries for cc-fleet-health.

The SRE holds the watchdog + fleet-status lease (fleet_health_lease.py), but the
ruling draws two bright lines the SRE must NEVER cross, enforced here in code
(not by charter prose alone):

  (a) NO singleton-body reset authority. The destructive context /clear reset
      (context_health_watchdog.py `--arm=red`) stays CAI-500-gated and is a
      SEPARATE executor — cc-fleet-health NEVER passes red. Only the WRITE-ONLY
      amber-checkpoint half is ever armed for the SRE. `assert_no_sre_red_reset`
      fail-closes if the SRE identity is about to drive a red reset.

  (b) OPS, NOT GOVERNANCE. cc-fleet-health uses its OWN agent_id and must NEVER
      write `strategic_decisions` or grant rows — it monitors and self-heals, it
      does not rule or authorize. `assert_ops_only` fail-closes if an SRE code
      path attempts a governance write.

Fail-closed = raise `BoundaryViolation` (loud, non-swallowable) so a mis-wire is
a crash, not a silent overreach.
"""
from __future__ import annotations

import os

SRE_AGENT_ID = "cc-fleet-health"

# Tables/operations that are GOVERNANCE, never the SRE's to write.
GOVERNANCE_TABLES = frozenset({
    "strategic_decisions",
    "grants",
    "grant_requests",
    "authorization_grants",
    "residency_grants",
})


class BoundaryViolation(RuntimeError):
    """A cc-fleet-health hard boundary (CAI-RESP-501) was about to be crossed."""


def acting_identity() -> str:
    """The identity the current process acts AS for pen/boundary purposes.
    Mirrors fleet_health_lease._agent_id: default SRE; the hub declares itself
    via FLEET_HEALTH_LEASE_AS. NEVER derived from ORCH_BODY_ROLE."""
    return (os.environ.get("FLEET_HEALTH_LEASE_AS") or SRE_AGENT_ID).strip()


def running_as_sre(identity: str | None = None) -> bool:
    return (identity or acting_identity()) == SRE_AGENT_ID


def assert_no_sre_red_reset(arm_level: str, identity: str | None = None) -> None:
    """Boundary (a). Fail-closed if the SRE is about to drive a destructive red
    /clear reset. The SRE may run detect (off) and the WRITE-ONLY amber
    checkpoint half; red is a separate, CAI-500-gated executor it never invokes."""
    if arm_level == "red" and running_as_sre(identity):
        raise BoundaryViolation(
            "cc-fleet-health has NO singleton-body reset authority (CAI-RESP-501). "
            "The destructive context /clear reset (--arm=red) is a separate, "
            "CAI-500-gated executor; the SRE only ever arms the write-only amber "
            "checkpoint half. Refusing red under the SRE identity.")


def assert_ops_only(table: str, identity: str | None = None) -> None:
    """Boundary (b). Fail-closed if the SRE is about to write a governance table
    (strategic_decisions / grant rows). The SRE is ops-only."""
    if running_as_sre(identity) and table in GOVERNANCE_TABLES:
        raise BoundaryViolation(
            f"cc-fleet-health is OPS, not governance (CAI-RESP-501): it must NEVER "
            f"write '{table}'. Governance rows (strategic_decisions / grants) are "
            f"cai's, authored under its own identity — never the SRE's.")


def self_test() -> int:
    """Offline proof of both boundaries. No DB, no I/O."""
    failures: list[str] = []

    def ok(cond, msg):
        if not cond:
            failures.append(msg)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    # (a) red reset under SRE identity -> BoundaryViolation
    try:
        assert_no_sre_red_reset("red", identity=SRE_AGENT_ID)
        ok(False, "SRE red reset must raise")
    except BoundaryViolation:
        ok(True, "SRE red reset raises BoundaryViolation")

    # SRE amber / off allowed
    try:
        assert_no_sre_red_reset("amber", identity=SRE_AGENT_ID)
        assert_no_sre_red_reset("off", identity=SRE_AGENT_ID)
        ok(True, "SRE amber+off checkpoint half allowed")
    except BoundaryViolation:
        ok(False, "SRE amber/off must NOT raise")

    # hub (non-SRE) may drive red (still CAI-500-gated downstream)
    try:
        assert_no_sre_red_reset("red", identity="cc-orchestrator")
        ok(True, "hub identity may drive red (SRE guard is identity-scoped)")
    except BoundaryViolation:
        ok(False, "hub red must NOT raise the SRE guard")

    # (b) governance write under SRE identity -> BoundaryViolation
    for tbl in GOVERNANCE_TABLES:
        try:
            assert_ops_only(tbl, identity=SRE_AGENT_ID)
            ok(False, f"SRE write to {tbl} must raise")
        except BoundaryViolation:
            ok(True, f"SRE write to governance table {tbl} raises")

    # SRE ops-table write allowed
    try:
        assert_ops_only("agent_messages", identity=SRE_AGENT_ID)
        assert_ops_only("agent_status", identity=SRE_AGENT_ID)
        ok(True, "SRE ops writes (agent_messages/agent_status) allowed")
    except BoundaryViolation:
        ok(False, "SRE ops writes must NOT raise")

    print()
    if failures:
        print(f"BOUNDARY SELF-TEST FAILED ({len(failures)} failure(s))")
        return 1
    print("BOUNDARY SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(self_test())
