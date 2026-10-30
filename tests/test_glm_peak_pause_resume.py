"""Tests for scripts/glm_peak_pause_resume.py (orch-console #67477/#67479/#67504/#67507,
Musa op#30538/op#30543/op#30563, 2026-10-15 standing GLM-peak automation)."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import psycopg
import pytest

from scripts import bus_send as _bus_send_mod

_SPEC = importlib.util.spec_from_file_location(
    "glm_peak_pause_resume", Path(__file__).resolve().parent.parent / "scripts" / "glm_peak_pause_resume.py")
g = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(g)


def _utc(h, m=0, day=15):
    return dt.datetime(2026, 10, day, h, m, tzinfo=dt.timezone.utc)


def _empty_config(tmp_path):
    """orch-console #81981: main() now refuses to move any lane unless CONFIG_FILE
    exists and is valid JSON -- tests that only care about urgent_pool/nonurgent_action
    defaults (no excluded/pinned lanes) point CONFIG_FILE here instead of a nonexistent
    path, so they exercise the real 'config present, keys absent' default path rather
    than the now-fatal 'config missing entirely' path."""
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text("{}")
    return cfg


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
    # orch-console ruling #79984 (2026-10-28): paperclip-spike's live scope (memory-plan build,
    # fleet launcher PR-B, irsyad bus_send migration) is operator/irsyad-adjacent -> Claude-only
    assert g.is_hard_excluded("cc-paperclip-spike") is True
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
    assert s == {"window_date": "2026-10-15", "flipped": [], "paused": [], "deepseek": [], "resume_done": False}


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
                           token_resolver=lambda s, od: "/fake/syed-oauth-token",
                           policy_check=_allowed)
    assert r["result"] == "OK"
    assert ptr.read_text() == "glm-5.3"
    assert calls[0][-2:] == ["cosem-port", "/fake/syed-oauth-token"]


def test_flip_back_to_glm_apply_reports_token_resolve_failure(tmp_path):
    def boom(s, od):
        raise RuntimeError("no default token resolved for cosem-port")
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, token_resolver=boom,
                           policy_check=_allowed)
    assert r["result"] == "FAILED" and "no default token resolved" in r["detail"]


# ── #79897: the .<session>_model pointer must never be left drifted by a failed flip ──
def _allowed(session):
    return None


def _refused(session):
    from scripts.lib import provider_policy as pp
    raise pp.ProviderNotAllowedError(session, "zai", {"allowed_providers": ["anthropic"]}, client_key=None)


def _rc(code, out=""):
    def run(argv, **kw):
        run.calls.append(argv)
        class R: returncode = code; stdout = out; stderr = ""
        return R()
    run.calls = []
    return run


def _tok(s, od):
    return "/fake/syed-oauth-token"


def test_flip_back_refused_by_policy_never_writes_pointer_when_absent(tmp_path):
    # the #79890 shape (seen on cc-paperclip-spike, since hard-excluded by #79984): an unmapped
    # lane with no pointer, flip refused by policy -> still no pointer
    run = _rc(0)
    r = g.flip_back_to_glm("cc-unmapped-spike", tmp_path, apply=True, run=run,
                           token_resolver=_tok, policy_check=_refused)
    assert r["result"] == "REFUSED"
    assert not (tmp_path / ".cc-unmapped-spike_model").exists()
    assert run.calls == []


def test_flip_back_refused_by_policy_leaves_existing_pointer_byte_identical(tmp_path):
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("claude-sonnet-5\n")
    run = _rc(0)
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, run=run,
                           token_resolver=_tok, policy_check=_refused)
    assert r["result"] == "REFUSED" and ptr.read_text() == "claude-sonnet-5\n" and run.calls == []


def test_flip_back_policy_unreadable_fails_closed_without_touching_pointer(tmp_path):
    def unreadable(session):
        raise RuntimeError("provider_policy fetch failed: connection refused")
    run = _rc(0)
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, run=run,
                           token_resolver=_tok, policy_check=unreadable)
    assert r["result"] == "FAILED" and "policy check failed" in r["detail"]
    assert not (tmp_path / ".cosem-port_model").exists() and run.calls == []


@pytest.mark.parametrize("rc,out", [(1, ""), (4, ""), (5, "HOLD: staged composer text"),
                                    (7, "ERROR: GLM flip REFUSED by the provider_policy gate")])
def test_flip_back_switch_fails_before_relaunch_restores_prior_pointer(tmp_path, rc, out):
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("claude-sonnet-5")
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, run=_rc(rc, out),
                           token_resolver=_tok, policy_check=_allowed)
    assert r["result"] == "FAILED" and ptr.read_text() == "claude-sonnet-5"
    assert "pointer restored" in r["detail"]


def test_flip_back_switch_fails_before_relaunch_removes_pointer_that_did_not_exist(tmp_path):
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, run=_rc(5),
                           token_resolver=_tok, policy_check=_allowed)
    assert r["result"] == "FAILED" and not (tmp_path / ".cosem-port_model").exists()


@pytest.mark.parametrize("rc,out", [(7, "ERROR: tmux new-session failed for 'cosem-port'. Lane is DOWN"),
                                    (8, ""), (10, ""), (11, "")])
def test_flip_back_switch_fails_after_relaunch_keeps_glm_pointer_and_says_verify(tmp_path, rc, out):
    # past the kill-session the lane may already be on GLM: restoring would drift the OTHER way
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("claude-sonnet-5")
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, run=_rc(rc, out),
                           token_resolver=_tok, policy_check=_allowed)
    assert r["result"] == "FAILED" and ptr.read_text() == "glm-5.3"
    assert "VERIFY" in r["detail"]


def test_flip_back_switch_raising_restores_prior_pointer(tmp_path):
    ptr = tmp_path / ".cosem-port_model"
    ptr.write_text("claude-sonnet-5")
    def boom(argv, **kw):
        raise OSError("exec format error")
    r = g.flip_back_to_glm("cosem-port", tmp_path, apply=True, run=boom,
                           token_resolver=_tok, policy_check=_allowed)
    assert r["result"] == "FAILED" and ptr.read_text() == "claude-sonnet-5"


def test_main_peak_dry_run_dispatches_urgent_vs_pause(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(g, "CONFIG_FILE", _empty_config(tmp_path))
    monkeypatch.delenv("GLM_PEAK_URGENT_POOL", raising=False)
    monkeypatch.delenv("GLM_PEAK_NONURGENT_ACTION", raising=False)
    (tmp_path / ".cosem-port_model").write_text("glm-5.3")
    (tmp_path / ".cosem-exams_model").write_text("glm-5.3")
    monkeypatch.setattr(g, "read_urgent_flags", lambda sessions: {"cosem-port": True, "cosem-exams": False})
    rc = g.main([], now=_utc(7, 0))  # dry-run (no --apply)
    out = capsys.readouterr().out
    assert "URGENT->musa-oauth-token cosem-port: DRY" in out
    assert "pause cosem-exams: DRY" in out
    assert rc == 0


def test_main_peak_dry_run_honours_config_knobs(tmp_path, monkeypatch, capsys):
    """[orch-console #81291] GLM_PEAK_URGENT_POOL=syed + GLM_PEAK_NONURGENT_ACTION=
    deepseek change the dispatch, with NO code change — the whole point of the fix."""
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(g, "CONFIG_FILE", _empty_config(tmp_path))
    (tmp_path / ".cosem-port_model").write_text("glm-5.3")
    (tmp_path / ".cosem-exams_model").write_text("glm-5.3")
    monkeypatch.setattr(g, "read_urgent_flags", lambda sessions: {"cosem-port": True, "cosem-exams": False})
    monkeypatch.setenv("GLM_PEAK_URGENT_POOL", "syed")
    monkeypatch.setenv("GLM_PEAK_NONURGENT_ACTION", "deepseek")
    rc = g.main([], now=_utc(7, 0))
    out = capsys.readouterr().out
    assert "URGENT->syed-oauth-token cosem-port: DRY" in out
    assert "nonurgent->deepseek cosem-exams: DRY" in out
    assert rc == 0


def test_urgent_pool_token_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("GLM_PEAK_URGENT_POOL", "gazzabyte")
    with pytest.raises(RuntimeError, match="GLM_PEAK_URGENT_POOL"):
        g.urgent_pool_token()


def test_nonurgent_action_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("GLM_PEAK_NONURGENT_ACTION", "shrug")
    with pytest.raises(RuntimeError, match="GLM_PEAK_NONURGENT_ACTION"):
        g.nonurgent_action()


def test_urgent_pool_token_defaults_to_musa(tmp_path, monkeypatch):
    monkeypatch.delenv("GLM_PEAK_URGENT_POOL", raising=False)
    monkeypatch.setattr(g, "CONFIG_FILE", tmp_path / "nonexistent.json")
    assert g.urgent_pool_token() == g.MUSA_TOKEN


def test_nonurgent_action_defaults_to_pause(tmp_path, monkeypatch):
    monkeypatch.delenv("GLM_PEAK_NONURGENT_ACTION", raising=False)
    monkeypatch.setattr(g, "CONFIG_FILE", tmp_path / "nonexistent.json")
    assert g.nonurgent_action() == "pause"


def test_urgent_pool_token_reads_config_file_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("GLM_PEAK_URGENT_POOL", raising=False)
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"urgent_pool": "syed"}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.urgent_pool_token() == g.SYED_TOKEN


def test_nonurgent_action_reads_config_file_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("GLM_PEAK_NONURGENT_ACTION", raising=False)
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"nonurgent_action": "deepseek"}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.nonurgent_action() == "deepseek"


def test_env_override_wins_over_config_file(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"urgent_pool": "syed"}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    monkeypatch.setenv("GLM_PEAK_URGENT_POOL", "musa")
    assert g.urgent_pool_token() == g.MUSA_TOKEN


def test_flip_to_deepseek_dry_run_changes_nothing(tmp_path, monkeypatch):
    called = []
    r = g.flip_to_deepseek("cosem-port", tmp_path, apply=False, run=lambda *a, **k: called.append(a))
    assert r["result"] == "DRY"
    assert called == []


def test_flip_to_deepseek_apply_unpauses_and_writes_deepseek_pointer(tmp_path, monkeypatch):
    calls = []
    run = _rc(0)
    monkeypatch.setattr(g, "set_desired_state", lambda session, state: calls.append(("desired_state", session, state)))
    r = g.flip_to_deepseek("hc95-broker", tmp_path, apply=True, run=run,
                           token_resolver=_tok, policy_check=_allowed)
    assert r["result"] == "OK"
    assert (tmp_path / ".hc95-broker_model").read_text() == "deepseek-flash"
    assert ("desired_state", "hc95-broker", "up") in calls
    assert run.calls == [[str(tmp_path / "scripts" / "switch_lane_token.sh"), "--model-apply", "--drain",
                           "hc95-broker", "/fake/syed-oauth-token"]]


def test_flip_to_deepseek_policy_refusal_touches_nothing(tmp_path, monkeypatch):
    calls = []
    run = _rc(0)
    monkeypatch.setattr(g, "set_desired_state", lambda *a: calls.append(a))
    r = g.flip_to_deepseek("irsyad-coord", tmp_path, apply=True, run=run, policy_check=_refused)
    assert r["result"] == "REFUSED"
    assert calls == []
    assert run.calls == []
    assert not (tmp_path / ".irsyad-coord_model").exists()


def test_flip_to_deepseek_token_resolve_failure_touches_nothing(tmp_path, monkeypatch):
    calls = []
    run = _rc(0)

    def bad_resolver(s, d):
        raise RuntimeError("no default token resolved")

    monkeypatch.setattr(g, "set_desired_state", lambda *a: calls.append(a))
    r = g.flip_to_deepseek("oeh", tmp_path, apply=True, run=run,
                           token_resolver=bad_resolver, policy_check=_allowed)
    assert r["result"] == "FAILED" and "token resolve failed" in r["detail"]
    assert calls == [] and run.calls == []
    assert not (tmp_path / ".oeh_model").exists()


@pytest.mark.parametrize("rc,out", [(1, ""), (4, ""), (5, "HOLD: staged composer text"),
                                    (7, "ERROR: GLM flip REFUSED by the provider_policy gate")])
def test_flip_to_deepseek_switch_fails_before_relaunch_restores_prior_pointer(tmp_path, monkeypatch, rc, out):
    """[cc-quality PR#625 should-fix] flip_to_deepseek must restore the pointer on a
    pre-relaunch switch failure, same as flip_back_to_glm (#79897) — not dormant once
    GLM_PEAK_NONURGENT_ACTION=deepseek is actually armed."""
    monkeypatch.setattr(g, "set_desired_state", lambda *a: None)
    ptr = tmp_path / ".hc95-broker_model"
    ptr.write_text("glm-5.3")
    r = g.flip_to_deepseek("hc95-broker", tmp_path, apply=True, run=_rc(rc, out),
                           token_resolver=_tok, policy_check=_allowed)
    assert r["result"] == "FAILED" and ptr.read_text() == "glm-5.3"


def test_flip_to_deepseek_switch_fails_after_relaunch_leaves_pointer_at_deepseek(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "set_desired_state", lambda *a: None)
    ptr = tmp_path / ".hc95-broker_model"
    ptr.write_text("glm-5.3")
    r = g.flip_to_deepseek("hc95-broker", tmp_path, apply=True, run=_rc(6, "some other failure"),
                           token_resolver=_tok, policy_check=_allowed)
    assert r["result"] == "FAILED" and ptr.read_text() == "deepseek-flash"
    assert "VERIFY the lane's live model" in r["detail"]


def test_main_off_peak_reverses_both_flipped_and_deepseek_buckets(tmp_path, monkeypatch, capsys):
    """A deepseek-fallback lane was never paused (desired_state stayed 'up' the whole
    time) — off-peak must send it through flip_back_to_glm, same as a flipped-to-musa
    urgent lane, never resume_pause()."""
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(g, "CONFIG_FILE", _empty_config(tmp_path))
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": ["cosem-port"],
                  "paused": ["cosem-exams"], "deepseek": ["hc95-broker"], "resume_done": False})
    seen = []
    monkeypatch.setattr(g, "flip_back_to_glm", lambda s, *a, **k: (seen.append(("glm", s)) or {"session": s, "result": "DRY", "detail": "x"}))
    monkeypatch.setattr(g, "resume_pause", lambda s, *a, **k: (seen.append(("unpause", s)) or {"session": s, "result": "DRY", "detail": "x"}))
    rc = g.main([], now=_utc(11, 0))
    assert rc == 0
    assert ("glm", "cosem-port") in seen
    assert ("glm", "hc95-broker") in seen
    assert ("unpause", "cosem-exams") in seen
    assert ("unpause", "hc95-broker") not in seen


def test_main_offpeak_resumes_recorded_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(g, "CONFIG_FILE", _empty_config(tmp_path))
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
    monkeypatch.setattr(g, "CONFIG_FILE", _empty_config(tmp_path))
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": ["cosem-port"], "paused": [], "resume_done": True})
    rc = g.main([], now=_utc(11, 0))
    out = capsys.readouterr().out
    assert "nothing to resume" in out
    assert rc == 0


# ── check_hard_exclude_divergence (orch-console Q5, bus #74588 transition period) ────

class _FakeCursor:
    def __init__(self, policy_rows, override_rows):
        self._policy_rows = policy_rows
        self._override_rows = override_rows
        self._pending = None
        self.description = None

    def execute(self, sql, params=None):
        if "provider_policy_lane_override" in sql:
            self._pending = self._override_rows
            self.description = [("lane",), ("allowed_providers",), ("text_only",)]
        else:
            self._pending = self._policy_rows
            self.description = [("client_key",), ("allowed_providers",), ("text_only",)]

    def fetchall(self):
        return self._pending

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, policy_rows, override_rows):
        self._policy_rows = policy_rows
        self._override_rows = override_rows

    def cursor(self):
        return _FakeCursor(self._policy_rows, self._override_rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _connect_with(policy_rows, override_rows):
    def _connect(dsn, **k):
        return _FakeConn(policy_rows, override_rows)
    return _connect


def test_divergence_check_no_disagreement_posts_nothing(monkeypatch):
    # cosem-port isn't hard-excluded (old=False) and resolves to an unmapped
    # client_key with an empty policy table -> ANTHROPIC_ONLY -> new ALSO says
    # excluded=True for zai... to keep old==new here, give it a zai-allowing row.
    policy_rows = [("cosem", ["anthropic", "zai"], True)]
    monkeypatch.setattr(psycopg, "connect", _connect_with(policy_rows, []))
    monkeypatch.setattr(_bus_send_mod, "dburl", lambda env: "postgres://fake")
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body)))
    g.check_hard_exclude_divergence("cosem-port", True)
    assert pages == []


def test_divergence_check_disagreement_posts_one_loud_warning(monkeypatch):
    # mamadah is NOT in HARD_EXCLUDE_LANES (old=False) but a correctly-anthropic-
    # only policy row means the new gate says excluded=True -- a real divergence.
    policy_rows = [("mamadah", ["anthropic"], False)]
    monkeypatch.setattr(psycopg, "connect", _connect_with(policy_rows, []))
    monkeypatch.setattr(_bus_send_mod, "dburl", lambda env: "postgres://fake")
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body, apply)))
    g.check_hard_exclude_divergence("cc-mamadah-1", True)
    assert len(pages) == 1
    subject, body, apply = pages[0]
    assert "cc-mamadah-1" in subject
    assert "old=False" in subject and "new=True" in subject
    assert apply is True


def test_divergence_check_hard_excluded_lane_with_matching_new_verdict_posts_nothing(monkeypatch):
    # cosem-adcda-photos: old=True (HARD_EXCLUDE_LANES); give it a matching
    # anthropic-only override so the new gate also says excluded=True -- no drift.
    policy_rows = [("cosem-adcda", ["anthropic", "zai"], True)]
    override_rows = [("cosem-adcda-photos", ["anthropic"], False)]
    monkeypatch.setattr(psycopg, "connect", _connect_with(policy_rows, override_rows))
    monkeypatch.setattr(_bus_send_mod, "dburl", lambda env: "postgres://fake")
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body)))
    g.check_hard_exclude_divergence("cosem-adcda-photos", True)
    assert pages == []


def test_divergence_check_misconfigured_override_posts_a_warning(monkeypatch):
    policy_rows = [("irsyad", ["anthropic"], False)]
    override_rows = [("irsyad-worker-1", ["anthropic", "zai"], True)]  # WIDENS -- invalid
    monkeypatch.setattr(psycopg, "connect", _connect_with(policy_rows, override_rows))
    monkeypatch.setattr(_bus_send_mod, "dburl", lambda env: "postgres://fake")
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body)))
    g.check_hard_exclude_divergence("irsyad-worker-1", True)
    assert len(pages) == 1
    assert "misconfigured" in pages[0][0]


def test_divergence_check_db_failure_is_swallowed_never_raises(monkeypatch):
    def boom(dsn, **k):
        raise RuntimeError("db unreachable")
    monkeypatch.setattr(psycopg, "connect", boom)
    monkeypatch.setattr(_bus_send_mod, "dburl", lambda env: "postgres://fake")
    # must not raise, and must not call page() at all
    pages = []
    monkeypatch.setattr(g, "page", lambda *a, **k: pages.append(a))
    g.check_hard_exclude_divergence("anything", True)
    assert pages == []


def test_divergence_check_no_dsn_systemexit_is_swallowed_never_raises(monkeypatch):
    # the real failure mode seen in practice: bus_send.dburl() raises SystemExit
    # (not a plain Exception) when DATABASE_URL is unset.
    def boom(env):
        raise SystemExit("no DATABASE_URL")
    monkeypatch.setattr(_bus_send_mod, "dburl", boom)
    pages = []
    monkeypatch.setattr(g, "page", lambda *a, **k: pages.append(a))
    g.check_hard_exclude_divergence("anything", True)
    assert pages == []


# ── pins + excludes (orch-console #81954, 2026-10-29) ──────────────────────────
def test_config_lane_set_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "CONFIG_FILE", tmp_path / "nonexistent.json")
    assert g.config_lane_set("excluded_lanes") == set()


def test_config_lane_set_empty_when_key_missing(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"urgent_pool": "syed"}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.config_lane_set("pinned_lanes") == set()


def test_config_lane_set_reads_both_keys_independently(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({
        "excluded_lanes": ["hc95-broker", "oeh"],
        "pinned_lanes": ["cosem-adcda-urgent"],
    }))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.config_lane_set("excluded_lanes") == {"hc95-broker", "oeh"}
    assert g.config_lane_set("pinned_lanes") == {"cosem-adcda-urgent"}


def test_config_lane_set_rejects_non_list_value(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"excluded_lanes": "hc95-broker"}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    with pytest.raises(RuntimeError, match="must be a list"):
        g.config_lane_set("excluded_lanes")


def test_is_config_excluded_and_is_pinned(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({
        "excluded_lanes": ["hc95-broker"],
        "pinned_lanes": ["cosem-adcda-urgent"],
    }))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.is_config_excluded("hc95-broker") is True
    assert g.is_config_excluded("cosem-adcda-urgent") is False
    assert g.is_pinned("cosem-adcda-urgent") is True
    assert g.is_pinned("hc95-broker") is False


def test_is_blocked_priority_and_reasons(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({
        "excluded_lanes": ["hc95-broker"],
        "pinned_lanes": ["cosem-adcda-urgent"],
    }))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.is_blocked("cosem-adcda-photos") == "hard-excluded (standing never-list)"
    assert g.is_blocked("hc95-broker") == "config-excluded (provider managed elsewhere, e.g. DeepSeek rollout)"
    assert g.is_blocked("cosem-adcda-urgent") == "pinned (console hold)"
    assert g.is_blocked("cosem-port") is None


def test_flip_back_to_glm_skips_config_excluded_lane(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"excluded_lanes": ["hc95-broker"]}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    monkeypatch.setattr(g, "check_hard_exclude_divergence", lambda *a, **k: None)
    (tmp_path / ".hc95-broker_model").write_text("deepseek-flash")
    r = g.flip_back_to_glm("hc95-broker", tmp_path, apply=True)
    assert r["result"] == "SKIPPED"
    assert "config-excluded" in r["detail"]
    assert (tmp_path / ".hc95-broker_model").read_text() == "deepseek-flash"  # untouched


def test_flip_back_to_glm_skips_pinned_lane(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"pinned_lanes": ["cosem-adcda-urgent"]}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    monkeypatch.setattr(g, "check_hard_exclude_divergence", lambda *a, **k: None)
    (tmp_path / ".cosem-adcda-urgent_model").write_text("claude-sonnet-5")
    r = g.flip_back_to_glm("cosem-adcda-urgent", tmp_path, apply=True)
    assert r["result"] == "SKIPPED"
    assert "pinned" in r["detail"]
    assert (tmp_path / ".cosem-adcda-urgent_model").read_text() == "claude-sonnet-5"  # untouched


def test_main_peak_dispatch_skips_pinned_and_excluded_lanes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"pinned_lanes": ["cosem-port"]}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    (tmp_path / ".cosem-port_model").write_text("glm-5.3")
    monkeypatch.setattr(g, "read_urgent_flags", lambda sessions: {"cosem-port": True})
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body)))
    rc = g.main([], now=_utc(7, 0))  # dry-run
    out = capsys.readouterr().out
    assert "blocked cosem-port: pinned (console hold)" in out
    assert rc == 0
    # a SKIPPED-via-pin result still gets its own per-move log row
    assert any("cosem-port" in subj for subj, _ in pages)


def test_main_offpeak_resume_honours_pin_on_flipped_lane(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"pinned_lanes": ["cosem-adcda-urgent"]}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    monkeypatch.setattr(g, "check_hard_exclude_divergence", lambda *a, **k: None)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": ["cosem-adcda-urgent"], "paused": [],
                  "deepseek": [], "resume_done": False})
    (tmp_path / ".cosem-adcda-urgent_model").write_text("claude-sonnet-5")
    rc = g.main([], now=_utc(11, 0))  # off-peak, apply=False so no real bus_send
    out = capsys.readouterr().out
    assert "resume->glm cosem-adcda-urgent: SKIPPED — pinned (console hold)" in out
    assert rc == 0
    assert (tmp_path / ".cosem-adcda-urgent_model").read_text() == "claude-sonnet-5"  # untouched


def test_main_offpeak_resume_honours_exclude_on_deepseek_lane(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"excluded_lanes": ["hc95-broker"]}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    monkeypatch.setattr(g, "check_hard_exclude_divergence", lambda *a, **k: None)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": [], "paused": [],
                  "deepseek": ["hc95-broker"], "resume_done": False})
    (tmp_path / ".hc95-broker_model").write_text("deepseek-flash")
    rc = g.main([], now=_utc(11, 0))
    out = capsys.readouterr().out
    assert "resume->glm hc95-broker: SKIPPED — config-excluded" in out
    assert rc == 0
    assert (tmp_path / ".hc95-broker_model").read_text() == "deepseek-flash"  # untouched -- the whole point


def test_main_offpeak_resume_honours_pin_on_paused_lane(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"pinned_lanes": ["cosem-tdu-coord"]}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    monkeypatch.setattr(g, "check_hard_exclude_divergence", lambda *a, **k: None)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": [], "paused": ["cosem-tdu-coord"],
                  "deepseek": [], "resume_done": False})
    set_desired_calls = []
    monkeypatch.setattr(g, "resume_pause", lambda s, apply: set_desired_calls.append(s) or {"session": s, "result": "OK", "detail": "x"})
    rc = g.main([], now=_utc(11, 0))
    out = capsys.readouterr().out
    assert "resume-unpause cosem-tdu-coord: SKIPPED — pinned (console hold)" in out
    assert set_desired_calls == []  # resume_pause() never called -- the pin blocked it before dispatch
    assert rc == 0


def test_log_move_sends_a_bus_row_per_lane(monkeypatch):
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body, apply)))
    g.log_move("peak", {"session": "cosem-port", "result": "OK", "detail": "model=claude-sonnet-5"}, True)
    assert len(pages) == 1
    subject, body, apply = pages[0]
    assert "cosem-port" in subject and "OK" in subject
    assert "model=claude-sonnet-5" in body
    assert apply is True


def test_validate_pin_exclude_config_warns_on_orphan_entry(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"excluded_lanes": ["ffsfulfilment"]}))  # wrong form -- real pointer is .cc-ffsfulfilment_model
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    (tmp_path / ".cc-ffsfulfilment_model").write_text("deepseek-flash")
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body)))
    g.validate_pin_exclude_config(tmp_path, True)
    assert len(pages) == 1
    assert "orphan" in pages[0][0]
    assert "ffsfulfilment" in pages[0][1]


def test_validate_pin_exclude_config_silent_when_all_match(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"excluded_lanes": ["hc95-broker"], "pinned_lanes": ["cosem-adcda-urgent"]}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    (tmp_path / ".hc95-broker_model").write_text("deepseek-flash")
    (tmp_path / ".cosem-adcda-urgent_model").write_text("claude-sonnet-5")
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body)))
    g.validate_pin_exclude_config(tmp_path, True)
    assert pages == []


def test_validate_pin_exclude_config_silent_when_lists_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "CONFIG_FILE", tmp_path / "nonexistent.json")
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply: pages.append((subject, body)))
    g.validate_pin_exclude_config(tmp_path, True)
    assert pages == []


# ── config_readable() fail-closed gate (orch-console #81981, binding ruling on
# cc-quality's should-fix #2: missing/malformed config == ZERO lane moves, not
# "assume nothing is excluded") ──────────────────────────────────────────────
def test_config_readable_true_for_valid_json(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"urgent_pool": "syed"}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.config_readable() is True


def test_config_readable_false_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "CONFIG_FILE", tmp_path / "nonexistent.json")
    assert g.config_readable() is False


def test_config_readable_false_for_malformed_json(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text("{not valid json")
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    assert g.config_readable() is False


def test_main_peak_makes_zero_moves_when_config_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(g, "CONFIG_FILE", tmp_path / "nonexistent.json")
    (tmp_path / ".cosem-port_model").write_text("glm-5.3")  # a real peak candidate, if the gate failed open
    called = []
    monkeypatch.setattr(g, "flip_to_musa", lambda *a, **k: called.append("flip_to_musa"))
    monkeypatch.setattr(g, "pause_lane", lambda *a, **k: called.append("pause_lane"))
    monkeypatch.setattr(g, "flip_to_deepseek", lambda *a, **k: called.append("flip_to_deepseek"))
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply, **kw: pages.append((subject, body, apply, kw)))
    rc = g.main(["--apply"], now=_utc(7, 0))  # peak window
    out = capsys.readouterr().out
    assert called == []  # no mutator was ever invoked
    assert rc == 1
    assert "CONFIG UNREADABLE" in out
    assert len(pages) == 1
    subject, body, apply, kw = pages[0]
    assert "NO lane moves" in subject
    assert apply is True
    assert kw.get("priority") == "P1"
    assert set(kw.get("to", [])) == {"orch-console", "cc-fleet-health"}


def test_main_offpeak_makes_zero_moves_when_config_malformed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(g, "ORCH_DIR", tmp_path)
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text("{not valid json")
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    g.save_state({"window_date": today, "flipped": ["cosem-adcda-urgent"], "paused": ["cosem-tdu-coord"],
                  "deepseek": ["hc95-broker"], "resume_done": False})
    called = []
    monkeypatch.setattr(g, "flip_back_to_glm", lambda *a, **k: called.append("flip_back_to_glm"))
    monkeypatch.setattr(g, "resume_pause", lambda *a, **k: called.append("resume_pause"))
    pages = []
    monkeypatch.setattr(g, "page", lambda subject, body, apply, **kw: pages.append((subject, body, apply, kw)))
    rc = g.main(["--apply"], now=_utc(11, 0))  # off-peak window
    out = capsys.readouterr().out
    assert called == []  # not even flip_back_to_glm's own internal gate ran -- main() never dispatched
    assert rc == 1
    assert "CONFIG UNREADABLE" in out
    assert len(pages) == 1
    assert pages[0][3].get("priority") == "P1"


def test_page_prints_dry_for_every_target(capsys):
    g.page("subject", "body", False, to=["orch-console", "cc-fleet-health"])
    out = capsys.readouterr().out
    assert "[DRY] would page orch-console: subject" in out
    assert "[DRY] would page cc-fleet-health: subject" in out


def test_config_readable_false_for_permission_denied(tmp_path, monkeypatch):
    cfg = tmp_path / "glm_peak_pool_config.json"
    cfg.write_text(json.dumps({"urgent_pool": "syed"}))
    monkeypatch.setattr(g, "CONFIG_FILE", cfg)
    monkeypatch.setattr(g.Path, "read_text", lambda self: (_ for _ in ()).throw(PermissionError("denied")))
    assert g.config_readable() is False
