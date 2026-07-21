"""Pure-unit tests for fleet_health_lease + fleet_health_boundaries (CAI-RESP-501).

No DB. Exercises the pure decision/CAS core (`decide`, `apply_take`,
`apply_renew`) which the DB layer mirrors in atomic SQL, and both hard
boundaries. Proves the load-bearing invariant: NEVER two concurrent holders.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))

import fleet_health_lease as fhl
import fleet_health_boundaries as fhb

T0 = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
SRE = "cc-fleet-health"
HUB = "cc-orchestrator"
MINI = "Sheikhs-Mini"
STUDIO = "mac-studio"


def _row(holder=SRE, host=MINI, renewed=T0, ttl=900):
    return {"holder": holder, "holder_host": host, "acquired_at": renewed,
            "renewed_at": renewed, "ttl_seconds": ttl, "taken_over_from": None,
            "takeover_reason": None}


# ── decide() — the pen gate ──────────────────────────────────────────────────

def test_decide_missing_row_failsafe():
    ok, _ = fhl.decide(None, SRE, MINI, T0)
    assert ok is True  # never strand the monitor pre-migration


def test_decide_null_holder_failsafe():
    ok, _ = fhl.decide(_row(holder=None), SRE, MINI, T0)
    assert ok is True


def test_decide_holder_current_allows():
    ok, r = fhl.decide(_row(SRE, MINI), SRE, MINI, T0 + timedelta(seconds=60))
    assert ok is True and r == "holder-current"


def test_decide_null_host_allows_recorded_holder():
    # seed state: holder set, host NULL, not yet self-stamped
    ok, _ = fhl.decide(_row(SRE, None), SRE, MINI, T0 + timedelta(seconds=60))
    assert ok is True


def test_decide_holder_stale_self_still_allows():
    # my lease expired but no one reclaimed (holder still me) -> proceed + renew
    ok, r = fhl.decide(_row(SRE, MINI), SRE, MINI, T0 + timedelta(seconds=2000))
    assert ok is True and r == "holder-stale-self"


def test_decide_different_fresh_holder_REFUSES():
    # hub holds a fresh lease -> the SRE daemon must defer (fail-closed)
    ok, r = fhl.decide(_row(HUB, STUDIO), SRE, MINI, T0 + timedelta(seconds=60))
    assert ok is False and "deferred" in r


def test_decide_different_expired_holder_reclaim_eligible():
    ok, r = fhl.decide(_row(HUB, STUDIO), SRE, MINI, T0 + timedelta(seconds=2000))
    assert ok is True and "reclaim-eligible" in r


def test_never_two_holders_on_a_fresh_lease():
    """The invariant: exactly one body may act while a lease is FRESH."""
    fresh = _row(SRE, MINI, renewed=T0)
    now = T0 + timedelta(seconds=120)
    sre_ok, _ = fhl.decide(fresh, SRE, MINI, now)
    hub_ok, _ = fhl.decide(fresh, HUB, STUDIO, now)
    assert sre_ok is True and hub_ok is False  # only the SRE acts


# ── apply_take() — CAS acquire / reclaim ─────────────────────────────────────

def test_take_idempotent_when_already_mine():
    new = fhl.apply_take(_row(SRE, MINI), SRE, MINI, T0 + timedelta(seconds=60))
    assert new is not None and new["holder"] == SRE


def test_take_refuses_to_steal_a_fresh_other_holder():
    # hub freshly holds it -> SRE take must FAIL (return None), never steal
    new = fhl.apply_take(_row(HUB, STUDIO), SRE, MINI, T0 + timedelta(seconds=60),
                         reason="boot")
    assert new is None


def test_take_reclaims_an_expired_other_holder():
    new = fhl.apply_take(_row(HUB, STUDIO), SRE, MINI, T0 + timedelta(seconds=2000),
                         reason="hub dead")
    assert new is not None
    assert new["holder"] == SRE and new["holder_host"] == MINI
    assert new["taken_over_from"] == f"{HUB}@{STUDIO}"
    assert new["takeover_reason"] == "hub dead"


def test_hub_reclaims_expired_sre_lease():
    # the dead-man fallback: SRE lease expired, hub reclaims
    new = fhl.apply_take(_row(SRE, MINI), HUB, STUDIO, T0 + timedelta(seconds=2000),
                         reason="SRE stalled")
    assert new is not None and new["holder"] == HUB
    assert new["taken_over_from"] == f"{SRE}@{MINI}"


def test_take_none_row():
    assert fhl.apply_take(None, SRE, MINI, T0) is None


def test_reclaim_then_original_holder_defers():
    """Full dead-man cutover: hub reclaims an expired SRE lease; the SRE's zombie
    daemon then sees a fresh hub lease and defers -> never two."""
    reclaimed = fhl.apply_take(_row(SRE, MINI), HUB, STUDIO,
                               T0 + timedelta(seconds=2000), reason="SRE dead")
    # reclaimed lease is now fresh (renewed_at bumped to the reclaim time)
    now = T0 + timedelta(seconds=2000)
    sre_ok, _ = fhl.decide(reclaimed, SRE, MINI, now + timedelta(seconds=10))
    hub_ok, _ = fhl.decide(reclaimed, HUB, STUDIO, now + timedelta(seconds=10))
    assert hub_ok is True and sre_ok is False


# ── apply_renew() — heartbeat ────────────────────────────────────────────────

def test_renew_holder_ok():
    new = fhl.apply_renew(_row(SRE, MINI, renewed=T0), SRE, MINI,
                          T0 + timedelta(seconds=300))
    assert new is not None and new["renewed_at"] == T0 + timedelta(seconds=300)


def test_renew_self_stamps_null_host():
    new = fhl.apply_renew(_row(SRE, None), SRE, MINI, T0 + timedelta(seconds=1))
    assert new is not None and new["holder_host"] == MINI


def test_renew_refuses_non_holder():
    # renew never acquires — a non-holder must fail (use take instead)
    assert fhl.apply_renew(_row(HUB, STUDIO), SRE, MINI, T0) is None


# ── hard boundaries ──────────────────────────────────────────────────────────

def test_boundary_self_test_passes():
    assert fhb.self_test() == 0


def test_sre_red_reset_blocked():
    with pytest.raises(fhb.BoundaryViolation):
        fhb.assert_no_sre_red_reset("red", identity=SRE)


def test_sre_amber_reset_allowed():
    fhb.assert_no_sre_red_reset("amber", identity=SRE)  # no raise


def test_hub_red_reset_allowed_by_sre_guard():
    fhb.assert_no_sre_red_reset("red", identity=HUB)  # identity-scoped, no raise


def test_sre_governance_write_blocked():
    with pytest.raises(fhb.BoundaryViolation):
        fhb.assert_ops_only("strategic_decisions", identity=SRE)


def test_sre_ops_write_allowed():
    fhb.assert_ops_only("agent_messages", identity=SRE)  # no raise
