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

# Singleton-class bodies the SRE may NEVER red-reset, even by misconfig
# (CAI-RESP-681 cond 3 + CAI-RESP-705 cond 4, hard). A red /clear of cai / the hub
# / Nazim stays HUB-ONLY. cc-fleet-health (SRE_AGENT_ID) is here too as the SELF
# guard (CAI-705): the SRE is a lease-held singleton-class body and must never
# recycle ITSELF mid-run — that would drop the fleet_health_lease + kill the very
# process driving the reset (the self-recycle gap cai flagged at arm time). Worker
# lanes are everything NOT in this set.
SINGLETON_BODIES = frozenset({"cc-orchestrator", "cai", "orch-console", SRE_AGENT_ID})

# The three gates that must ALL be mechanically verified True before the SRE may
# red-reset a worker lane (CAI-RESP-681 conditions 1+2). fresh_handoff is the
# load-bearing one — it's what makes the reset lossless. Any gate that is not
# explicitly True (False / None / missing / unverifiable) FAILS CLOSED: no reset.
SRE_LANE_RED_GATES = ("idle", "git_clean", "fresh_handoff")

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


def assert_sre_never_targets_singleton(target_agent_id: str, identity: str | None = None) -> None:
    """CAI-RESP-681 condition 3 (HARD). The SRE may NEVER red-reset a singleton
    body (cai / the hub / Nazim), even by misconfig — that stays HUB-ONLY.
    Fail-closed. This is a SEPARATE, always-on guard from the lanes-only carve-out
    below: even if the arming/gate logic were mis-wired, this refuses at the
    target level so the three critical nodes are structurally unreachable to the
    SRE's red path."""
    if running_as_sre(identity) and target_agent_id in SINGLETON_BODIES:
        selfnote = (" — the SRE never recycles ITSELF (CAI-705 self-guard): that "
                    "would drop its own lease + kill the driving process"
                    if target_agent_id == SRE_AGENT_ID else "")
        raise BoundaryViolation(
            f"cc-fleet-health may NEVER red-reset the singleton-class body "
            f"'{target_agent_id}' (CAI-RESP-681 cond 3 / CAI-705 cond 4). SRE "
            f"red-reset is WORKER-LANES-ONLY; cai / the hub / Nazim stay hub-only, "
            f"fail-closed{selfnote}.")


def assert_sre_lane_red_permitted(
    target_agent_id: str,
    gates: "dict",
    armed: bool,
    identity: str | None = None,
) -> None:
    """The lanes-only SRE red-reset carve-out (CAI-RESP-681, operator op#9141).

    Fail-closed permission check the SRE MUST call immediately before any
    worker-lane /clear. Raises BoundaryViolation if ANY condition is not met;
    returns None (permits) only when all are satisfied. Non-SRE identities are
    not gated here (the hub's red path is separate) but the singleton guard still
    applies to everyone.

    Enforces cai's binding conditions:
      3 (HARD): target must be a WORKER LANE, never a singleton — checked first,
        always, so a misconfigured target can't slip through.
      5: `armed` must be True. The carve-out is STANDING-ARMED by default since
        CAI-RESP-1382 (the post-director-demo precondition was met >1mo ago;
        CAI-RESP-1381 confirmed the temporary disarm just sat stale). This CHECK
        stays the mechanism: a caller that explicitly force-disarms (--no-arm /
        CTX_WD_LANE_ARM=0 -> armed=False) still refuses here, fail-closed.
      1+2: every gate in SRE_LANE_RED_GATES must be EXPLICITLY True. idle +
        git_clean + fresh_handoff; fresh_handoff is load-bearing (a stale/absent
        handoff makes the reset lossy). Anything not exactly True — False, None,
        missing key, unverifiable — fails closed: "if you can't confirm it, no
        reset" (don't assume, check).
    """
    # cond 3 FIRST + unconditionally: singletons are structurally off-limits.
    assert_sre_never_targets_singleton(target_agent_id, identity)

    # The carve-out only gates the SRE identity; a non-SRE (hub) red path is
    # governed elsewhere. Still, we've already run the singleton guard above.
    if not running_as_sre(identity):
        return

    # cond 5: STANDING-ARMED by default (CAI-RESP-1381/1382). The check STAYS the
    # mechanism — an explicit force-disarm (--no-arm / CTX_WD_LANE_ARM=0) still refuses.
    if armed is not True:
        raise BoundaryViolation(
            "SRE lane red-reset is force-DISARMED (CAI-RESP-681 cond 5) — refusing. "
            "The carve-out is standing-armed by default (CAI-RESP-1382); this run was "
            "explicitly disarmed (--no-arm / CTX_WD_LANE_ARM=0).")

    # cond 1+2: all three gates mechanically verified True, else fail-closed.
    if not isinstance(gates, dict):
        raise BoundaryViolation(
            "SRE lane red-reset gates unverifiable (no gate map) — fail-closed.")
    for g in SRE_LANE_RED_GATES:
        if gates.get(g) is not True:
            raise BoundaryViolation(
                f"SRE lane red-reset gate '{g}' is not verified True "
                f"(got {gates.get(g)!r}) — fail-closed (CAI-RESP-681 cond 1/2: "
                f"idle + git-clean + FRESH handoff, all mechanically confirmed; "
                f"if it can't be confirmed, no reset).")


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

    # ── CAI-RESP-681: SRE lanes-only red-reset carve-out ──────────────────────
    ALL_TRUE = {"idle": True, "git_clean": True, "fresh_handoff": True}

    def raises(fn, msg):
        try:
            fn(); ok(False, msg + " (should have raised)")
        except BoundaryViolation:
            ok(True, msg)

    def permits(fn, msg):
        try:
            fn(); ok(True, msg)
        except BoundaryViolation as e:
            ok(False, msg + f" (raised: {e})")

    # cond 3 HARD: SRE can NEVER target a singleton, even armed + all gates true.
    for body in SINGLETON_BODIES:
        raises(lambda b=body: assert_sre_never_targets_singleton(b, identity=SRE_AGENT_ID),
               f"SRE red on singleton '{body}' hard-refused")
        raises(lambda b=body: assert_sre_lane_red_permitted(b, ALL_TRUE, True, identity=SRE_AGENT_ID),
               f"SRE lane-gate on singleton '{body}' refused (armed+all-gates notwithstanding)")

    # Happy path: worker lane, armed, all three gates verified True -> PERMIT.
    permits(lambda: assert_sre_lane_red_permitted("cc-cosem-video", ALL_TRUE, True, identity=SRE_AGENT_ID),
            "SRE lane red permitted: worker lane + armed + all gates True")

    # cond 5: disarmed -> refuse even with all gates true.
    raises(lambda: assert_sre_lane_red_permitted("cc-cosem-video", ALL_TRUE, False, identity=SRE_AGENT_ID),
           "SRE lane red refused when DISARMED (cond 5)")

    # cond 1/2: each gate must be EXPLICITLY True. False / None / missing all fail closed.
    for bad_key, bad_val, label in [
        ("idle", False, "idle=False"),
        ("git_clean", False, "git_clean=False"),
        ("fresh_handoff", False, "fresh_handoff=False (load-bearing)"),
        ("idle", None, "idle=None (unverifiable)"),
        ("fresh_handoff", None, "fresh_handoff=None (unverifiable)"),
    ]:
        g = dict(ALL_TRUE); g[bad_key] = bad_val
        raises(lambda g=g: assert_sre_lane_red_permitted("cc-cosem-video", g, True, identity=SRE_AGENT_ID),
               f"SRE lane red refused when {label}")
    # missing key entirely -> fail closed
    raises(lambda: assert_sre_lane_red_permitted("cc-cosem-video", {"idle": True, "git_clean": True}, True, identity=SRE_AGENT_ID),
           "SRE lane red refused when a gate key is MISSING")
    # gates not a dict -> fail closed
    raises(lambda: assert_sre_lane_red_permitted("cc-cosem-video", None, True, identity=SRE_AGENT_ID),
           "SRE lane red refused when gate map is not a dict")

    # identity-scoping: a NON-SRE (hub) is not gated by the carve-out for a lane,
    # but the singleton guard is identity-scoped too (only bites the SRE).
    permits(lambda: assert_sre_lane_red_permitted("cc-cosem-video", {}, False, identity="cc-orchestrator"),
            "non-SRE (hub) not gated by the SRE lanes carve-out")
    permits(lambda: assert_sre_never_targets_singleton("cai", identity="cc-orchestrator"),
            "non-SRE (hub) may target a singleton (SRE guard is identity-scoped)")

    print()
    if failures:
        print(f"BOUNDARY SELF-TEST FAILED ({len(failures)} failure(s))")
        return 1
    print("BOUNDARY SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(self_test())
