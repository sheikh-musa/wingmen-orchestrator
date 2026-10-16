"""Tests for scripts/glm_peak_pause_resume.py (orch-console #67477/#67479/#67504/#67507,
Musa op#30538/op#30543/op#30563, 2026-10-15 standing GLM-peak automation)."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "glm_peak_pause_resume", Path(__file__).resolve().parent.parent / "scripts" / "glm_peak_pause_resume.py")
g = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(g)


def _utc(h, m=0, day=15):
    return dt.datetime(2026, 10, day, h, m, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize("hour,minute,expected", [
    (5, 59, False), (6, 0, True), (7, 30, True), (9, 59, True), (10, 0, False), (14, 0, False),
])
def test_in_peak_window(hour, minute, expected):
    assert g.in_peak_window(_utc(hour, minute)) is expected


@pytest.mark.parametrize("day,expected_weekday", [(17, "Saturday"), (18, "Sunday")])
def test_in_peak_window_skips_weekends_even_during_peak_hours(day, expected_weekday):
    assert _utc(7, 30, day=day).strftime("%A") == expected_weekday
    assert g.in_peak_window(_utc(7, 30, day=day)) is False


def test_in_peak_window_true_on_weekday_at_peak_hour():
    assert _utc(7, 30, day=16).strftime("%A") == "Friday"
    assert g.in_peak_window(_utc(7, 30, day=16)) is True


def test_is_hard_excluded_covers_the_standing_never_list():
    # glm-rollout-plan-20261013 line 35: NEVER move these onto GLM.
    assert g.is_hard_excluded("cosem-adcda-photos") is True
    assert g.is_hard_excluded("cc-fleet-health") is True
    assert g.is_hard_excluded("irsyad-worker-1") is True
    assert g.is_hard_excluded("irsyad-coord") is True
    assert g.is_hard_excluded("orch-console") is True
    assert g.is_hard_excluded("cosem-port") is False
    assert g.is_hard_excluded("cosem-adcda") is False


def test_glm_pointer_lanes_excludes_hard_barred_even_with_a_glm_pointer(tmp_path):
    (tmp_path / ".cosem-port_model").write_text("glm-5.3")
    (tmp_path / ".cosem-adcda-photos_model").write_text("glm-5.3")  # must never be a candidate
    (tmp_path / ".finance_model").write_text("claude-opus-4-8")
    (tmp_path / ".cosem-port_model.bak-glmoverflow-20261007T1200Z").write_text("glm-5.3")
    assert g.glm_pointer_lanes(tmp_path) == ["cosem-port"]


def test_peak_candidates_skips_already_handled():
    lanes = ["cosem-port", "cosem-exams", "finance"]
    handled = {"cosem-exams"}
    assert g.peak_candidates(lanes, handled) == ["cosem-port", "finance"]


def test_fresh_state_for_shape():
    s = g.fresh_state_for("2026-10-15")
    assert s == {"window_date": "2026-10-15", "flipped": [], "paused": [], "resume_done": False}


def test_load_state_resets_on_new_day(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    g.save_state({"window_date": "2026-10-14", "flipped": ["x"], "paused": [], "resume_done": True})
    s = g.load_state()
    assert s["window_date"] == dt.datetime.now(dt.timezone.utc).date().isoformat()
    assert s["flipped"] == [] and s["paused"] == [] and s["resume_done"] is False


def test_load_state_keeps_same_day_state(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": ["cosem-port"], "paused": ["cosem-exams"], "resume_done": False})
    s = g.load_state()
    assert s["flipped"] == ["cosem-port"] and s["paused"] == ["cosem-exams"]


def test_flip_to_musa_dry_run_changes_nothing(tmp_path, monkeypatch):
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("glm-5.3")
    monkeypatch.setattr(g, "read_lane_model", lambda s: "claude-sonnet-5")
    called = []
    r = g.flip_to_musa("cosem-port", tmp_path, apply=False, run=lambda *a, **k: called.append(a))
    assert r["result"] == "DRY" and ptr.read_text() == "glm-5.3" and not called


def test_flip_to_musa_apply_writes_pointer_and_calls_switch(tmp_path, monkeypatch):
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("glm-5.3")
    monkeypatch.setattr(g, "read_lane_model", lambda s: "claude-sonnet-5")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        class R: returncode = 0; stdout = "RESULT: PASS"; stderr = ""
        return R()

    r = g.flip_to_musa("cosem-port", tmp_path, apply=True, run=fake_run)
    assert r["result"] == "OK"
    assert ptr.read_text() == "claude-sonnet-5"
    assert calls[0][-2:] == ["cosem-port", g.MUSA_TOKEN]
    assert "--drain" in calls[0] and "--model-apply" in calls[0]


def test_flip_to_musa_apply_reports_failed_switch(tmp_path, monkeypatch):
    (tmp_path / ".cosem-port_model").write_text("glm-5.3")
    monkeypatch.setattr(g, "read_lane_model", lambda s: "claude-sonnet-5")

    def fake_run(argv, **kw):
        class R: returncode = 3; stdout = ""; stderr = "ERROR: busy\n"
        return R()

    r = g.flip_to_musa("cosem-port", tmp_path, apply=True, run=fake_run)
    assert r["result"] == "FAILED" and "ERROR: busy" in r["detail"]


def test_pause_lane_dry_run_vs_apply(monkeypatch):
    assert g.pause_lane("cosem-exams", apply=False)["result"] == "DRY"
    calls = []
    monkeypatch.setattr(g, "set_desired_state", lambda s, state: calls.append((s, state)))
    r = g.pause_lane("cosem-exams", apply=True)
    assert r["result"] == "OK" and calls == [("cosem-exams", "paused")]


def test_resume_pause_dry_run_vs_apply(monkeypatch):
    assert g.resume_pause("cosem-exams", apply=False)["result"] == "DRY"
    calls = []
    monkeypatch.setattr(g, "set_desired_state", lambda s, state: calls.append((s, state)))
    r = g.resume_pause("cosem-exams", apply=True)
    assert r["result"] == "OK" and calls == [("cosem-exams", "up")]


def test_flip_back_to_glm_never_touches_a_hard_excluded_lane(tmp_path):
    r = g.flip_back_to_glm("cosem-adcda-photos", tmp_path, apply=True)
    assert r["result"] == "SKIPPED"
    assert not (tmp_path / ".cosem-adcda-photos_model").exists()


def test_flip_back_to_glm_dry_run_changes_nothing(tmp_path):
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("claude-sonnet-5")
    called = []
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=False, run=lambda *a, **k: called.append(a))
    assert r["result"] == "DRY" and ptr.read_text() == "claude-sonnet-5" and not called


def test_flip_back_to_glm_apply_writes_glm_pointer_and_resolves_default_token(tmp_path):
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("claude-sonnet-5")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        class R: returncode = 0; stdout = "RESULT: PASS"; stderr = ""
        return R()

    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, run=fake_run,
                           token_resolver=lambda s, od: "/fake/syed-oauth-token")
    assert r["result"] == "OK"
    assert ptr.read_text() == "glm-5.3"
    assert calls[0][-2:] == ["cosem-port", "/fake/syed-oauth-token"]


def test_flip_back_to_glm_apply_reports_token_resolve_failure(tmp_path):
    def boom(s, od):
        raise RuntimeError("no default token resolved for cosem-port")
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, token_resolver=boom)
    assert r["result"] == "FAILED" and "no default token resolved" in r["detail"]


def test_main_peak_dry_run_dispatches_urgent_vs_pause(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    (tmp_path / ".cosem-port_model").write_text("glm-5.3")
    (tmp_path / ".cosem-exams_model").write_text("glm-5.3")
    monkeypatch.setattr(g, "read_urgent_flags", lambda sessions: {"cosem-port": True, "cosem-exams": False})
    rc = g.main([], now=_utc(7, 0))  # dry-run (no --apply)
    out = capsys.readouterr().out
    assert "URGENT->musa cosem-port: DRY" in out
    assert "pause cosem-exams: DRY" in out
    assert rc == 0


def test_main_offpeak_resumes_recorded_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": ["cosem-port"], "paused": ["cosem-exams"], "resume_done": False})
    rc = g.main([], now=_utc(11, 0))  # dry-run
    out = capsys.readouterr().out
    assert "resume->glm cosem-port: DRY" in out
    assert "resume-unpause cosem-exams: DRY" in out
    assert rc == 0


def test_main_offpeak_noop_once_resume_done(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": ["cosem-port"], "paused": [], "resume_done": True})
    rc = g.main([], now=_utc(11, 0))
    out = capsys.readouterr().out
    assert "nothing to resume" in out
    assert rc == 0
