"""Tests for scripts/glm_overflow_switch.py (Musa op#27156, orch-console #58028 rule)."""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "glm_overflow_switch", Path(__file__).resolve().parent.parent / "scripts" / "glm_overflow_switch.py")
g = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(g)


@pytest.mark.parametrize("wk,h5,m7,m5,action", [
    (87.5, 51.7, 8, 7, "none"),            # today 11:45Z
    (94.9, 94.9, 8, 7, "none"),            # just under both triggers
    (95.0, 10.0, 8, 7, "switch"),          # weekly trigger, inclusive
    (99.0, 99.0, 8, 7, "switch"),
    (90.0, 95.0, 8, 7, "switch"),          # 5h trigger with weekly >= 90 (inclusive)
    (89.9, 96.0, 8, 7, "page_5h_only"),    # 5h trigger but weekly low -> wait for reset
    (96.0, 10.0, 90, 7, "page_musa_high"), # Musa 7d too high
    (96.0, 10.0, 8, 90, "page_musa_high"), # Musa 5h too high
    (91.0, 97.0, 8, 95, "page_musa_high"), # 5h-path switch also guarded by Musa headroom
    (50.0, 99.0, 99, 99, "page_5h_only"),  # 5h-only never consults Musa (no switch due)
])
def test_decide_rule(wk, h5, m7, m5, action):
    assert g.decide(wk, h5, m7, m5)["action"] == action


def test_decide_reason_names_the_numbers():
    r = g.decide(96.2, 40.0, 8, 7)
    assert "96.2%" in r["reason"] and "Musa" in r["reason"]


def test_pct_normalises_fractions_and_refuses_missing():
    assert g.pct(0.07) == pytest.approx(7.0)
    assert g.pct(42.0) == 42.0
    with pytest.raises(RuntimeError):
        g.pct(None)


def test_glm_lanes_reads_only_live_glm_pointers(tmp_path):
    (tmp_path / ".cosem-port_model").write_text("glm-5.3\n")
    (tmp_path / ".exams_model").write_text("glm-5.3")
    (tmp_path / ".finance_model").write_text("claude-opus-4-8")
    (tmp_path / ".cosem-port_model.bak-glm-20261006").write_text("glm-5.3")   # backups ignored
    (tmp_path / ".shipforge_model.bak-glmoverflow-20261007T1200Z").write_text("glm-5.3")
    assert g.glm_lanes(tmp_path) == ["cosem-port", "exams"]


def test_dry_run_switch_changes_nothing(tmp_path, monkeypatch):
    ptr = tmp_path / ".exams_model"
    ptr.write_text("glm-5.3")
    monkeypatch.setattr(g, "tmux_up", lambda s: True)
    called = []
    r = g.switch_lane("exams", tmp_path, apply=False, run=lambda *a, **k: called.append(a))
    assert r["result"] == "DRY" and ptr.exists() and not called


def test_apply_on_down_lane_moves_pointer_only(tmp_path, monkeypatch):
    ptr = tmp_path / ".cosem-exams_model"
    ptr.write_text("glm-5.3")
    monkeypatch.setattr(g, "tmux_up", lambda s: False)
    called = []
    r = g.switch_lane("cosem-exams", tmp_path, apply=True, run=lambda *a, **k: called.append(a))
    assert r["result"] == "POINTER-ONLY" and not ptr.exists() and not called
    assert list(tmp_path.glob(".cosem-exams_model.bak-glmoverflow-*"))


def test_apply_live_lane_ok_when_argv_leaves_glm(tmp_path, monkeypatch):
    (tmp_path / ".exams_model").write_text("glm-5.3")
    monkeypatch.setattr(g, "tmux_up", lambda s: True)
    monkeypatch.setattr(g, "lane_argv_model", lambda s: "claude-sonnet-5")

    class CP:
        returncode, stdout, stderr = 0, "PASS", ""
    r = g.switch_lane("exams", tmp_path, apply=True, run=lambda *a, **k: CP())
    assert r["result"] == "OK" and "claude-sonnet-5" in r["detail"]


def test_apply_live_lane_pending_when_still_on_glm(tmp_path, monkeypatch):
    (tmp_path / ".exams_model").write_text("glm-5.3")
    monkeypatch.setattr(g, "tmux_up", lambda s: True)
    monkeypatch.setattr(g, "lane_argv_model", lambda s: "glm-5.3")

    class CP:
        returncode, stdout, stderr = 1, "", "ERROR: 'exams' is BUSY"
    r = g.switch_lane("exams", tmp_path, apply=True, run=lambda *a, **k: CP())
    # pointer stays moved so the next run (or any relaunch) lands on Musa; never restored to GLM
    assert r["result"] == "PENDING" and not (tmp_path / ".exams_model").exists()


# ── retry of PENDING lanes (found live 2026-10-07: pass 1 left 5 lanes on GLM and the
#    next run could not see them because their pointers had already moved) ──────────
def test_pending_lane_is_retried_after_pointer_moved(tmp_path):
    (tmp_path / ".cosem-port_model.bak-glmoverflow-20261007T124545Z").write_text("glm-5.3")
    (tmp_path / ".exams_model.bak-glmoverflow-20261007T124545Z").write_text("glm-5.3")
    argv = {"cosem-port": "glm-5.3", "exams": "claude-sonnet-5"}.get
    assert g.pending_lanes(tmp_path, argv) == ["cosem-port"]   # exams already switched


def test_pending_ignored_when_pointer_restored_or_lane_down(tmp_path):
    (tmp_path / ".a_model.bak-glmoverflow-20261007T1Z").write_text("glm-5.3")
    (tmp_path / ".a_model").write_text("claude-opus-4-8")             # operator re-pointed it
    (tmp_path / ".b_model.bak-glmoverflow-20261007T1Z").write_text("glm-5.3")  # lane down
    assert g.pending_lanes(tmp_path, {"a": "glm-5.3", "b": None}.get) == []


def test_lanes_to_switch_unions_and_dedupes(tmp_path):
    (tmp_path / ".cosem-adcda-urgent_model").write_text("glm-5.3")
    (tmp_path / ".cosem-port_model.bak-glmoverflow-20261007T1Z").write_text("glm-5.3")
    (tmp_path / ".cosem-port_model.bak-glmoverflow-20261007T2Z").write_text("glm-5.3")  # 2 passes
    got = g.lanes_to_switch(tmp_path, lambda s: "glm-5.3")
    assert got == ["cosem-adcda-urgent", "cosem-port"]


def test_switch_lane_on_pending_lane_relaunches_without_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "tmux_up", lambda s: True)
    monkeypatch.setattr(g, "lane_argv_model", lambda s: "claude-sonnet-5")
    calls = []

    class CP:
        returncode, stdout, stderr = 0, "PASS", ""
    r = g.switch_lane("cosem-port", tmp_path, apply=True, run=lambda *a, **k: (calls.append(a), CP())[1])
    assert r["result"] == "OK" and calls and not list(tmp_path.glob(".cosem-port_model*"))
