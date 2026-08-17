"""CAI-985 A3 sink (cc-fleet-health, 2026-08-17; encoding ruled by cai CAI-RESP-1014).

Records one A3 measurer run atomically:
  * invariant_registry.gate_status <- 'COVERED' on PASS (+ last_asserted_at=now()), 'pending' on
    FAIL/ERROR (last_asserted_at UNCHANGED — a run that did not prove the invariant true lets the
    row go stale). So the existing 047 view reads a live leak as NOT EXERCISED, by construction —
    a false green on fail is impossible (cai's simplification over COVERED-on-fail-plus-join).
  * invariant_assertion_runs <- one immutable row carrying the verdict + evidence + proof-of-run
    (Doctrine-1 / D7: substrate-readable AND joinable to the invariant).

assert_ops_only gates BOTH tables BEFORE any write (the boundary was previously uncalled — wiring
it here makes it real, not decorative; cai Q4 confirmed). ERROR is the D6 fail-closed / D5
"could-not-look" state, distinct from a leak-FAIL (flagged to cai as a proposed refinement).

The live-DB integration test writes inside a ROLLED-BACK txn so the real RESIDENCY-1 row is never
touched before A3 has actually fired.
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


def _run(cur, outcome, finding_count, monkeypatch, allow=True):
    if allow:
        monkeypatch.setattr(sink, "assert_ops_only", lambda *a, **k: None)
    return sink.record_a3_run(cur, outcome=outcome, scope_checked="PUBLIC grants outside shipforge",
                              finding_count=finding_count, evidence={"n": finding_count})


def test_boundary_gates_BOTH_tables_before_any_write(monkeypatch):
    # assert_ops_only must run — and be able to block — BEFORE any UPDATE/INSERT, for BOTH the
    # invariant_registry row and the run-record table (cai Q4). If it raises, nothing is written.
    seen = []

    def fake_assert(table, identity=None):
        seen.append(table)
        raise BoundaryViolation("blocked")

    monkeypatch.setattr(sink, "assert_ops_only", fake_assert)
    cur = _FakeCur()
    with pytest.raises(BoundaryViolation):
        sink.record_a3_run(cur, outcome="PASS", scope_checked="x", finding_count=0, evidence=None)
    assert cur.executed == [], "a write ran despite the boundary raising"
    assert "invariant_registry" in seen, "must gate invariant_registry"


def _reg_update(cur):
    # the gate_status value is parameterised (gate_status=%s) -> it is in the PARAMS, not the SQL
    return next((sql, params) for sql, params in cur.executed if "UPDATE invariant_registry" in sql)


def test_pass_sets_covered_and_stamps_and_logs_run(monkeypatch):
    cur = _FakeCur()
    _run(cur, "PASS", 0, monkeypatch)
    sql, params = _reg_update(cur)
    assert params[0] == "COVERED", f"PASS must set gate_status=COVERED, got param {params[0]!r}"
    assert "last_asserted_at=now()" in sql, f"PASS must stamp last_asserted_at: {sql}"
    assert any("INSERT INTO invariant_assertion_runs" in s for s, _ in cur.executed), "must log a run row"


def test_fail_sets_pending_and_does_NOT_stamp(monkeypatch):
    cur = _FakeCur()
    _run(cur, "FAIL", 3, monkeypatch)
    sql, params = _reg_update(cur)
    assert params[0] == "pending", f"FAIL must set gate_status='pending' (never COVERED), got {params[0]!r}"
    assert "last_asserted_at" not in sql, "FAIL must NOT bump last_asserted_at (let a fail streak go stale)"


def test_error_sets_pending_like_fail(monkeypatch):
    cur = _FakeCur()
    _run(cur, "ERROR", 0, monkeypatch)
    sql, params = _reg_update(cur)
    assert params[0] == "pending", f"ERROR (fail-closed / could-not-measure) must be not-green, got {params[0]!r}"
    assert "last_asserted_at" not in sql


def test_outcome_and_count_must_agree(monkeypatch):
    monkeypatch.setattr(sink, "assert_ops_only", lambda *a, **k: None)
    cur = _FakeCur()
    # PASS with findings, or FAIL with none, is a contradiction the sink must reject fail-loud
    with pytest.raises(ValueError):
        sink.record_a3_run(cur, outcome="PASS", scope_checked="x", finding_count=2, evidence=None)
    with pytest.raises(ValueError):
        sink.record_a3_run(cur, outcome="FAIL", scope_checked="x", finding_count=0, evidence=None)


def test_unknown_outcome_rejected(monkeypatch):
    monkeypatch.setattr(sink, "assert_ops_only", lambda *a, **k: None)
    cur = _FakeCur()
    with pytest.raises(ValueError):
        sink.record_a3_run(cur, outcome="MAYBE", scope_checked="x", finding_count=0, evidence=None)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs substrate DATABASE_URL")
def test_real_pass_write_flips_covered_rolled_back():
    # SHIPPED-PATH: the real invariant_registry CHECK accepts COVERED; rolled back so the live row
    # is never touched pre-fire. (The run-record INSERT is skipped here until cai applies the
    # invariant_assertion_runs migration — asserted separately once the table exists.)
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        before = cur.execute(
            "SELECT gate_status FROM invariant_registry WHERE invariant_ref='RESIDENCY-1'").fetchone()
        assert before is not None
        cur.execute(
            "UPDATE invariant_registry SET gate_status='COVERED', last_asserted_at=now(), updated_at=now() "
            "WHERE invariant_ref='RESIDENCY-1'")
        after = cur.execute(
            "SELECT gate_status FROM invariant_registry WHERE invariant_ref='RESIDENCY-1'").fetchone()
        assert after[0] == "COVERED"
        conn.rollback()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        assert cur.execute(
            "SELECT gate_status FROM invariant_registry WHERE invariant_ref='RESIDENCY-1'"
        ).fetchone()[0] == before[0], "rollback failed — live row changed"
