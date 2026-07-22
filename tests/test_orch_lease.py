"""Pure-unit tests for orch_lease's expiry-aware pen gate + CAS take/renew.

No DB. Exercises the pure decision/CAS core (`decide`, `apply_take`,
`apply_renew`, `_is_expired`) which the DB layer mirrors in atomic SQL. Proves
the load-bearing invariant introduced by the TTL fix: a HEALTHY, recently-
renewed holder is PROTECTED from a mistaken-death `take`, while a genuinely-
expired lease stays takeable and the DR break-glass (--force) is preserved.

Mirrors tests/test_fleet_health_lease.py, but the orch-hub body identity is its
HOST (holder_host), not an agent_id.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))

import orch_lease as ol

T0 = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
HUB = "cc-orchestrator"
STUDIO = "mac-studio"
MINI = "Sheikhs-Mini"


def _row(holder=HUB, host=STUDIO, renewed=T0, ttl=900):
    return {"holder": holder, "holder_host": host, "acquired_at": renewed,
            "renewed_at": renewed, "ttl_seconds": ttl, "taken_over_from": None,
            "takeover_reason": None}


# ── _is_expired() ────────────────────────────────────────────────────────────

def test_is_expired_within_ttl_false():
    assert ol._is_expired(T0, 900, T0 + timedelta(seconds=899)) is False


def test_is_expired_after_ttl_true():
    assert ol._is_expired(T0, 900, T0 + timedelta(seconds=901)) is True


# ── decide() — the pen gate ──────────────────────────────────────────────────

def test_decide_missing_row_failsafe():
    ok, _ = ol.decide(None, STUDIO, T0)
    assert ok is True  # never strand the hub pre-migration


def test_decide_null_host_failsafe():
    # seed state: holder set, host NULL, not yet self-stamped
    ok, _ = ol.decide(_row(HUB, None), STUDIO, T0 + timedelta(seconds=60))
    assert ok is True


def test_decide_holder_current_allows():
    ok, r = ol.decide(_row(HUB, STUDIO), STUDIO, T0 + timedelta(seconds=60))
    assert ok is True and r == "holder-current"


def test_decide_holder_stale_self_still_allows():
    # my lease expired but no one reclaimed (host still me) -> proceed + renew
    ok, r = ol.decide(_row(HUB, STUDIO), STUDIO, T0 + timedelta(seconds=2000))
    assert ok is True and r == "holder-stale-self"


def test_decide_different_fresh_holder_REFUSES():
    # Studio holds a FRESH lease -> the Mini/console body must defer (fail-closed).
    # This is the core protection: a healthy holder is NOT stealable.
    ok, r = ol.decide(_row(HUB, STUDIO), MINI, T0 + timedelta(seconds=60))
    assert ok is False and "refused" in r


def test_decide_different_expired_holder_reclaim_eligible():
    ok, r = ol.decide(_row(HUB, STUDIO), MINI, T0 + timedelta(seconds=2000))
    assert ok is True and "reclaim-eligible" in r


def test_never_two_holders_on_a_fresh_lease():
    """The invariant: exactly one body may act while a lease is FRESH."""
    fresh = _row(HUB, STUDIO, renewed=T0)
    now = T0 + timedelta(seconds=120)
    studio_ok, _ = ol.decide(fresh, STUDIO, now)
    mini_ok, _ = ol.decide(fresh, MINI, now)
    assert studio_ok is True and mini_ok is False  # only Studio acts


# ── apply_take() — CAS acquire / DR takeover ─────────────────────────────────

def test_take_idempotent_when_already_this_host():
    new = ol.apply_take(_row(HUB, STUDIO), HUB, STUDIO, T0 + timedelta(seconds=60))
    assert new is not None and new["holder_host"] == STUDIO


def test_take_refuses_to_steal_a_fresh_other_holder():
    # THE FIX: Studio freshly holds it -> a Mini `take` must FAIL (return None),
    # never steal a healthy holder on a mistaken belief it's dead.
    new = ol.apply_take(_row(HUB, STUDIO), HUB, MINI, T0 + timedelta(seconds=60),
                        reason="thought it was dead")
    assert new is None


def test_take_reclaims_an_expired_other_holder():
    new = ol.apply_take(_row(HUB, STUDIO), HUB, MINI, T0 + timedelta(seconds=2000),
                        reason="hub dead")
    assert new is not None
    assert new["holder_host"] == MINI
    assert new["taken_over_from"] == f"{HUB}@{STUDIO}"
    assert new["takeover_reason"] == "hub dead"


def test_force_break_glass_steals_a_fresh_holder():
    # DR break-glass preserved: --force takes over even a FRESH live holder.
    new = ol.apply_take(_row(HUB, STUDIO), HUB, MINI, T0 + timedelta(seconds=60),
                        reason="split-brain, holder confirmed dead", force=True)
    assert new is not None and new["holder_host"] == MINI
    assert new["taken_over_from"] == f"{HUB}@{STUDIO}"


def test_force_snapshot_cas_rejects_concurrent_double_force():
    # CAI-RESP-513: two concurrent `take --force` must NOT lost-update (split-brain).
    # Both responders read the SAME snapshot; the first force wins; the row is now
    # changed; the second force still holding the STALE snapshot CAS-FAILS (None)
    # instead of clobbering the first takeover.
    snapshot = _row(HUB, STUDIO)                       # what both responders read
    now = T0 + timedelta(seconds=60)                   # holder still fresh -> force needed
    # Responder A: row still matches snapshot -> force succeeds, moves it to MINI.
    a = ol.apply_take(_row(HUB, STUDIO), HUB, MINI, now,
                      reason="A: holder dead?", force=True, snapshot=snapshot)
    assert a is not None and a["holder_host"] == MINI
    # Responder B forces LATER against the row A already changed, but with the
    # ORIGINAL snapshot -> snapshot-CAS fails -> None (B sees contention, re-reads).
    b = ol.apply_take(dict(a), HUB, STUDIO, now,
                      reason="B: holder dead?", force=True, snapshot=snapshot)
    assert b is None


def test_snapshot_cas_matches_when_row_unchanged():
    # Single legit --force against a live hub: snapshot matches the row -> succeeds
    # (renewed_at bumping is NOT part of the CAS, so a heartbeat between read/write
    # doesn't spuriously fail the take).
    snapshot = _row(HUB, STUDIO, renewed=T0)
    row_now = _row(HUB, STUDIO, renewed=T0 + timedelta(seconds=30))  # holder renewed, same holder/host
    new = ol.apply_take(row_now, HUB, MINI, T0 + timedelta(seconds=60),
                        reason="single DR", force=True, snapshot=snapshot)
    assert new is not None and new["holder_host"] == MINI


def test_take_none_row():
    assert ol.apply_take(None, HUB, STUDIO, T0) is None


def test_take_bumps_renewed_at():
    now = T0 + timedelta(seconds=2000)
    new = ol.apply_take(_row(HUB, STUDIO), HUB, MINI, now, reason="dead")
    assert new["renewed_at"] == now and new["acquired_at"] == now


def test_reclaim_then_original_host_defers():
    """Full cutover: Mini reclaims an expired Studio lease; Studio's zombie body
    then sees a fresh Mini lease and defers -> never two."""
    reclaimed = ol.apply_take(_row(HUB, STUDIO), HUB, MINI,
                              T0 + timedelta(seconds=2000), reason="hub dead")
    now = T0 + timedelta(seconds=2000)
    mini_ok, _ = ol.decide(reclaimed, MINI, now + timedelta(seconds=10))
    studio_ok, _ = ol.decide(reclaimed, STUDIO, now + timedelta(seconds=10))
    assert mini_ok is True and studio_ok is False


# ── apply_renew() — heartbeat ────────────────────────────────────────────────

def test_renew_holder_ok():
    new = ol.apply_renew(_row(HUB, STUDIO, renewed=T0), STUDIO,
                         T0 + timedelta(seconds=300))
    assert new is not None and new["renewed_at"] == T0 + timedelta(seconds=300)


def test_renew_self_stamps_null_host():
    new = ol.apply_renew(_row(HUB, None), STUDIO, T0 + timedelta(seconds=1))
    assert new is not None and new["holder_host"] == STUDIO


def test_renew_refuses_non_holder():
    # renew never acquires — a different host must fail (use take instead)
    assert ol.apply_renew(_row(HUB, STUDIO), MINI, T0) is None


def test_renew_keeps_lease_fresh_protecting_from_takeover():
    """End-to-end of the fix: a live holder that renews stays un-expired, so a
    concurrent (non-force) take from another host keeps failing."""
    row = _row(HUB, STUDIO, renewed=T0, ttl=900)
    # holder renews at t+800 (before expiry)
    renewed = ol.apply_renew(row, STUDIO, T0 + timedelta(seconds=800))
    # a would-be thief tries at t+900 — lease renewed_at is now t+800, still fresh
    thief = ol.apply_take(renewed, HUB, MINI, T0 + timedelta(seconds=900),
                          reason="mistaken")
    assert thief is None
