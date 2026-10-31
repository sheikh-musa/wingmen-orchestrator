"""Tests for scripts/musa_deepseek_syed_switch.py (Musa op#36560/36562, orch-console
#86307/#86319)."""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "musa_deepseek_syed_switch",
    Path(__file__).resolve().parent.parent / "scripts" / "musa_deepseek_syed_switch.py")
g = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(g)


# ── decide() ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("musa_7d,workers_due,brains_due", [
    (85.0, False, False),
    (89.9, False, False),
    (90.0, True, False),     # inclusive
    (94.9, True, False),
    (95.0, True, True),      # inclusive; both fire together
    (99.0, True, True),
])
def test_decide(musa_7d, workers_due, brains_due):
    d = g.decide(musa_7d)
    assert d["workers_due"] == workers_due
    assert d["brains_due"] == brains_due


def test_pct_normalises_fractions_and_refuses_missing():
    assert g.pct(0.9) == pytest.approx(90.0)
    assert g.pct(85.0) == 85.0
    with pytest.raises(RuntimeError):
        g.pct(None)


# ── provider_policy pre-check (deepseek_cleared) ──────────────────────────────
def test_deepseek_cleared_true_for_a_cleared_client_key():
    rows = [{"client_key": "finance", "allowed_providers": ["anthropic", "zai", "deepseek"], "text_only": True}]
    assert g.deepseek_cleared("finance", rows, []) is True


def test_deepseek_cleared_false_fail_closed_for_unknown_session():
    rows = [{"client_key": "finance", "allowed_providers": ["anthropic", "zai", "deepseek"], "text_only": True}]
    assert g.deepseek_cleared("some-unmapped-lane", rows, []) is False


def test_deepseek_cleared_respects_narrowing_override():
    rows = [{"client_key": "finance", "allowed_providers": ["anthropic", "zai", "deepseek"], "text_only": True}]
    override = [{"lane": "finance", "allowed_providers": ["anthropic"], "text_only": False}]
    assert g.deepseek_cleared("finance", rows, override) is False


# ── worker lane switch (dry-run / apply / pointer-only / pending) ────────────
def test_dry_run_switch_changes_nothing(tmp_path, monkeypatch):
    ptr = tmp_path / ".finance_model"
    ptr.write_text("claude-sonnet-5")
    monkeypatch.setattr(g, "tmux_up", lambda s: True)
    called = []
    r = g.switch_worker_lane("finance", tmp_path, apply=False, run=lambda *a, **k: called.append(a))
    assert r["result"] == "DRY" and ptr.read_text() == "claude-sonnet-5" and not called


def test_apply_on_down_lane_moves_pointer_to_deepseek(tmp_path, monkeypatch):
    ptr = tmp_path / ".shipforge_model"
    ptr.write_text("claude-sonnet-5")
    monkeypatch.setattr(g, "tmux_up", lambda s: False)
    called = []
    r = g.switch_worker_lane("shipforge", tmp_path, apply=True, run=lambda *a, **k: called.append(a))
    assert r["result"] == "POINTER-ONLY" and not called
    assert ptr.read_text() == "deepseek-flash"
    assert list(tmp_path.glob(".shipforge_model.bak-musadeepseek-*"))


def test_apply_live_lane_ok_when_argv_shows_deepseek(tmp_path, monkeypatch):
    (tmp_path / ".finance_model").write_text("claude-sonnet-5")
    monkeypatch.setattr(g, "tmux_up", lambda s: True)
    monkeypatch.setattr(g, "lane_argv_model", lambda s: "deepseek-flash")

    class CP:
        returncode, stdout, stderr = 0, "PASS", ""
    r = g.switch_worker_lane("finance", tmp_path, apply=True, run=lambda *a, **k: CP())
    assert r["result"] == "OK" and "deepseek-flash" in r["detail"]
    assert (tmp_path / ".finance_model").read_text() == "deepseek-flash"


def test_apply_live_lane_pending_when_relaunch_refused(tmp_path, monkeypatch):
    (tmp_path / ".finance_model").write_text("claude-sonnet-5")
    monkeypatch.setattr(g, "tmux_up", lambda s: True)
    monkeypatch.setattr(g, "lane_argv_model", lambda s: "claude-sonnet-5")

    class CP:
        returncode, stdout, stderr = 1, "", "ERROR: 'finance' is BUSY"
    r = g.switch_worker_lane("finance", tmp_path, apply=True, run=lambda *a, **k: CP())
    assert r["result"] == "PENDING"
    # pointer stays moved to deepseek so a retry / any other relaunch also lands there
    assert (tmp_path / ".finance_model").read_text() == "deepseek-flash"


def test_switch_worker_lane_with_no_prior_pointer_writes_one(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "tmux_up", lambda s: False)
    r = g.switch_worker_lane("paperclip-spike", tmp_path, apply=True, run=lambda *a, **k: None)
    assert r["result"] == "POINTER-ONLY"
    assert (tmp_path / ".paperclip-spike_model").read_text() == "deepseek-flash"
    assert not list(tmp_path.glob(".paperclip-spike_model.bak-musadeepseek-*"))  # nothing to back up


# ── brain / mamadah already-moved ground truth ───────────────────────────────
def test_brains_already_moved_reads_ground_truth(monkeypatch):
    class FakeCur:
        def execute(self, *a, **k): pass
        def fetchall(self): return [("orch-console",), ("cc-quality",)]
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(g, "_db", lambda: FakeConn())
    moved = g.brains_already_moved("582043088eae")
    assert moved == {"cc-fleet-health": False, "orch-console": True, "cc-quality": True}


def test_brains_already_moved_fails_safe_on_db_error(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(g, "_db", boom)
    moved = g.brains_already_moved("582043088eae")
    assert all(v is False for v in moved.values())


def test_mamadah_already_moved_false_on_mismatch(monkeypatch):
    class FakeCur:
        def execute(self, *a, **k): pass
        def fetchone(self): return ("68142948c003",)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(g, "_db", lambda: FakeConn())
    assert g.mamadah_already_moved("582043088eae") is False


# ── switch_brain / switch_mamadah dry-run and apply, incl. durable pin ────────
def test_switch_brain_dry_run_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    called = []
    r = g.switch_brain("cc-fleet-health", apply=False, run=lambda *a, **k: called.append(a))
    assert r["result"] == "DRY" and not called
    assert not (tmp_path / ".fleet-health_default_token").exists()


def test_switch_brain_apply_writes_durable_pin_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)

    class CP:
        returncode, stdout, stderr = 0, "RESULT: PASS", ""
    r = g.switch_brain("cc-quality", apply=True, run=lambda *a, **k: CP())
    assert r["result"] == "OK"
    pin = tmp_path / ".quality_default_token"
    assert pin.exists() and str(g.SYED_TOKEN) in pin.read_text()


def test_switch_brain_apply_reports_failed_without_writing_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)

    class CP:
        returncode, stdout, stderr = 6, "", "ERROR: restore point missing"
    r = g.switch_brain("orch-console", apply=True, run=lambda *a, **k: CP())
    assert r["result"] == "FAILED"
    assert not (tmp_path / ".nazim_default_token").exists()


def test_switch_mamadah_dry_run(monkeypatch):
    called = []
    r = g.switch_mamadah(apply=False, run=lambda *a, **k: called.append(a))
    assert r["result"] == "DRY" and not called


def test_switch_mamadah_apply_ok(monkeypatch):
    class CP:
        returncode, stdout, stderr = 0, "PASS", ""
    r = g.switch_mamadah(apply=True, run=lambda *a, **k: CP())
    assert r["result"] == "OK"


# ── worker_candidate_sessions exclusions (mamadah / irsyad / protected) ──────
def test_worker_candidate_sessions_excludes_protected_and_special_lanes(monkeypatch):
    class FakeCur:
        def execute(self, *a, **k): pass
        def fetchall(self):
            return [
                ("cc-finance-1", "finance"),
                ("cc-mamadah-1", "mamadah"),
                ("cc-irsyad-2", "irsyad-worker-3"),
                ("cc-fleet-health", "fleet-health"),
                ("orch-console", "nazim"),
            ]
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(g, "_db", lambda: FakeConn())
    import sys
    import types
    fake_mod = types.ModuleType("nervous_system.protected_agents")
    fake_mod.protected_agent_ids = lambda: frozenset({"cc-fleet-health", "orch-console"})
    sys.modules["nervous_system.protected_agents"] = fake_mod
    out = g.worker_candidate_sessions("68142948c003")
    assert out == [("cc-finance-1", "finance")]


# ── DeepSeek balance coverage projection ──────────────────────────────────────
def test_project_deepseek_coverage_insufficient_points():
    r = g.project_deepseek_coverage(5, dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc), points=[])
    assert r["measurable"] is False


def test_project_deepseek_coverage_zero_current_lanes():
    pts = [(dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.timezone.utc), 44.0),
           (dt.datetime(2026, 10, 31, 11, 0, tzinfo=dt.timezone.utc), 43.0)]
    r = g.project_deepseek_coverage(5, dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc),
                                     points=pts, current_lanes=0)
    assert r["measurable"] is False


def test_project_deepseek_coverage_math_and_sufficiency():
    now = dt.datetime(2026, 10, 31, 11, 24, tzinfo=dt.timezone.utc)
    pts = [(dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.timezone.utc), 44.0),
           (now, 43.0)]  # $1.00 over 8.4h across 7 lanes
    until = dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc)
    r = g.project_deepseek_coverage(8, until, now=now, points=pts, current_lanes=7)
    assert r["measurable"] is True
    observed_rate = 1.0 / 8.4
    per_lane = observed_rate / 7
    assert r["per_lane_rate_usd_hr"] == pytest.approx(per_lane, rel=1e-6)
    assert r["projected_rate_usd_hr"] == pytest.approx(per_lane * 15, rel=1e-6)
    hours = (until - now).total_seconds() / 3600.0
    assert r["projected_spend"] == pytest.approx(per_lane * 15 * hours, rel=1e-6)
    assert r["sufficient"] is True  # ~$0.15/hr * 15 lanes * ~92.6h ≈ $29, well under $43


def test_project_deepseek_coverage_flags_insufficient():
    now = dt.datetime(2026, 10, 31, 11, 24, tzinfo=dt.timezone.utc)
    # Burn $20 in under an hour across 7 lanes -> clearly cannot cover a multi-day window.
    pts = [(now - dt.timedelta(minutes=30), 63.0), (now, 43.0)]
    until = now + dt.timedelta(days=4)
    r = g.project_deepseek_coverage(8, until, now=now, points=pts, current_lanes=7)
    assert r["measurable"] is True
    assert r["sufficient"] is False


def test_project_deepseek_coverage_sums_drops_across_a_topup(monkeypatch):
    """cc-quality fast-follow 1 (orch-console #86372/#86380): endpoint-only (first - last)
    would read a top-up as near-zero net spend. Spend $10 (50 -> 40), top up $30 (40 ->
    70), spend $5 (70 -> 65) over 10h -- real spend is $15, endpoint-only would compute
    (50 - 65) = -15 -> floored to $0. Sum-of-drops must report $15."""
    t0 = dt.datetime(2026, 10, 31, 0, 0, tzinfo=dt.timezone.utc)
    pts = [(t0, 50.0),
           (t0 + dt.timedelta(hours=3), 40.0),   # -10 (spend)
           (t0 + dt.timedelta(hours=5), 70.0),   # +30 (top-up, floored to 0 spend)
           (t0 + dt.timedelta(hours=10), 65.0)]  # -5 (spend)
    now = t0 + dt.timedelta(hours=10)
    r = g.project_deepseek_coverage(0, now + dt.timedelta(hours=1), now=now, points=pts, current_lanes=5)
    assert r["measurable"] is True
    assert r["per_lane_rate_usd_hr"] == pytest.approx((15.0 / 10.0) / 5, rel=1e-6)
    endpoint_only_rate = max(0.0, 50.0 - 65.0) / 10.0  # the old (wrong) formula: 0.0
    assert r["per_lane_rate_usd_hr"] * 5 != pytest.approx(endpoint_only_rate, abs=1e-9)


# ── live resets_at (fast-follow 2) ──────────────────────────────────────────────
def test_musa_reset_at_reads_live_value(monkeypatch):
    class FakeCur:
        def execute(self, *a, **k): pass
        def fetchone(self): return (dt.datetime(2026, 12, 1, 8, 0, tzinfo=dt.timezone.utc),)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(g, "_db", lambda: FakeConn())
    r = g.musa_reset_at(fallback=dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc))
    assert r == dt.datetime(2026, 12, 1, 8, 0, tzinfo=dt.timezone.utc)


def test_musa_reset_at_falls_back_on_missing_row(monkeypatch):
    class FakeCur:
        def execute(self, *a, **k): pass
        def fetchone(self): return None
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self): return FakeCur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(g, "_db", lambda: FakeConn())
    fallback = dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc)
    assert g.musa_reset_at(fallback=fallback) == fallback


def test_musa_reset_at_falls_back_on_db_error(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(g, "_db", boom)
    fallback = dt.datetime(2026, 11, 4, 8, 0, tzinfo=dt.timezone.utc)
    assert g.musa_reset_at(fallback=fallback) == fallback
