"""CAI-985 A3 sink core (cc-fleet-health, 2026-08-17; Nazim #24061 'start the sink now').

The A3 automation records into invariant_registry.RESIDENCY-1 that its measurer is LIVE and
freshly-run: gate_status='COVERED' + last_asserted_at=now() (invariant_registry_state, mig 047,
reads EXERCISED only when gate_status='COVERED' AND last_asserted_at<30d, gate_status dominating).

FORK-INDEPENDENT CORE ONLY. gate_status='COVERED' answers "is there a live measurer", NOT the
PASS/FAIL verdict — that lives in a joinable, substrate-readable run record whose encoding cai
owns (FORK 2, pending her ruling #24064). These tests pin (1) the ops/governance boundary is a
REAL precondition, not decoration — assert_ops_only runs BEFORE the write and gates it — and (2)
the write uses the CHECK-legal 'COVERED' token (NOT 'MEASURED', which is CHECK-illegal) against
RESIDENCY-1. The live-DB integration test writes inside a rolled-back txn so the real RESIDENCY-1
row is NEVER touched before A3 has actually fired (writing COVERED pre-fire is itself a false green).
"""
import os
import pytest

from scripts.lib.fleet_health_boundaries import BoundaryViolation
import scripts.lib.a3_invariant_sink as sink


class _FakeCur:
    rowcount = 1

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def test_boundary_is_a_real_precondition_not_decoration(monkeypatch):
    # If the ops/governance boundary blocks the write, the UPDATE must NOT run. This proves
    # assert_ops_only is a genuine precondition (called BEFORE the write), not a decorative
    # import — it is currently uncalled anywhere, so wiring it here is what makes it real.
    calls = []

    def fake_assert(table, identity=None):
        calls.append((table, identity))
        raise BoundaryViolation("simulated governance block")

    monkeypatch.setattr(sink, "assert_ops_only", fake_assert)
    cur = _FakeCur()
    with pytest.raises(BoundaryViolation):
        sink.record_measurer_live(cur)
    assert cur.executed == [], "UPDATE ran despite the boundary raising — boundary is not gating the write"
    assert calls and calls[0][0] == "invariant_registry", \
        f"must assert the invariant_registry boundary, got: {calls!r}"


def test_records_covered_against_residency_1(monkeypatch):
    monkeypatch.setattr(sink, "assert_ops_only", lambda *a, **k: None)  # boundary allows
    cur = _FakeCur()
    sink.record_measurer_live(cur)
    assert len(cur.executed) == 1, f"expected exactly one UPDATE, got {cur.executed!r}"
    sql, params = cur.executed[0]
    assert "invariant_registry" in sql
    assert "COVERED" in sql, "must write the CHECK-legal COVERED token"
    assert "MEASURED" not in sql, "MEASURED is CHECK-illegal (only COVERED/MANUAL/pending allowed)"
    assert "last_asserted_at" in sql, "must stamp last_asserted_at so the view can read EXERCISED"
    assert params == ("RESIDENCY-1",), f"must target RESIDENCY-1, got params {params!r}"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs substrate DATABASE_URL")
def test_real_write_accepted_by_check_and_flips_covered_rolled_back():
    # SHIPPED-PATH verification against the real substrate schema, inside a rolled-back txn so
    # the live RESIDENCY-1 row is never permanently touched (pre-fire COVERED would be a false
    # green). Proves the gate_status CHECK accepts COVERED and the column names are right.
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            before = cur.execute(
                "SELECT gate_status, last_asserted_at FROM invariant_registry WHERE invariant_ref='RESIDENCY-1'"
            ).fetchone()
            assert before is not None, "RESIDENCY-1 row must exist"
            sink.record_measurer_live(cur)  # assert_ops_only real; SRE identity is allowed
            after = cur.execute(
                "SELECT gate_status, last_asserted_at FROM invariant_registry WHERE invariant_ref='RESIDENCY-1'"
            ).fetchone()
            assert after[0] == "COVERED", f"expected COVERED within txn, got {after[0]!r}"
            assert after[1] is not None, "last_asserted_at must be stamped"
        conn.rollback()  # leave the live row exactly as it was (pre-fire)
    # confirm no permanent change
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        now_row = cur.execute(
            "SELECT gate_status FROM invariant_registry WHERE invariant_ref='RESIDENCY-1'"
        ).fetchone()
        assert now_row[0] == before[0], "rollback failed — live RESIDENCY-1 was permanently changed"
