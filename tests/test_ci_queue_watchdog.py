"""Tests for scripts/ci_queue_watchdog.py — no network, no DB.

v2 (orch-console #58373, after false pages #58367/#58368): busy-aware.
A single serial runner that is ONLINE + BUSY with a queue behind it is
throughput, not an outage. Page only when:
  * runner(s) online + all busy AND the oldest queued job is older than
    ETA + threshold, ETA = queue depth N x avg job duration ("not draining");
  * runner(s) offline with work queued;
  * runner online + IDLE with work queued (stuck pickup);
  * no runner registered for the label.
ONE collapsed page per (repo, label) per cooldown window, listing the jobs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import ci_queue_watchdog as w  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
REPO = "sheikh-musa/wingmen-orchestrator"
LBL = "cubeasht"
KEY = f"ci_queue:{REPO}:{LBL}"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(jid=1, status="queued", labels=("self-hosted", LBL), age_min=45,
         name="Lint & Test", started_ago=None):
    return {
        "id": jid, "run_id": 900 + jid, "name": name, "status": status,
        "labels": list(labels), "created_at": _iso(NOW - timedelta(minutes=age_min)),
        # GitHub fills started_at with a placeholder on queued jobs; must NOT be used for age.
        "started_at": _iso(NOW - timedelta(minutes=started_ago if started_ago is not None else 0)),
        "html_url": f"https://github.com/{REPO}/actions/runs/900/job/{jid}",
    }


def _done(jid, dur_min=8.0, ended_ago=10, labels=("self-hosted", LBL), conclusion="success"):
    end = NOW - timedelta(minutes=ended_ago)
    return {
        "id": jid, "status": "completed", "conclusion": conclusion, "labels": list(labels),
        "started_at": _iso(end - timedelta(minutes=dur_min)), "completed_at": _iso(end),
        "created_at": _iso(end - timedelta(minutes=dur_min + 5)),
    }


def _runner(status="online", busy=False, name="cubeasht-orchestrator",
            labels=("self-hosted", "Linux", "X64", LBL, "win-wsl")):
    return {"name": name, "status": status, "busy": busy, "labels": [{"name": l} for l in labels]}


def _ev(active, runners, completed=(), threshold=30, grace=5):
    return w.evaluate(REPO, LBL, list(active), list(runners), list(completed), threshold, grace, NOW)


# ---------------------------------------------------------------- the #58367 scenario

def test_todays_scenario_busy_draining_no_page():
    """Online + busy, 1 running + 3 queued, 8-min jobs, oldest queued 31 min -> NO page.
    ETA = 3 x 8 = 24; 31 < 24 + 30."""
    active = [_job(10, status="in_progress", started_ago=4, age_min=35),
              _job(11, age_min=31), _job(12, age_min=20), _job(13, age_min=12)]
    completed = [_done(100 + i, dur_min=8) for i in range(10)]
    assert _ev(active, [_runner(busy=True)], completed) == []


def test_busy_not_draining_pages_with_eta_wording():
    active = [_job(10, status="in_progress", started_ago=4),
              _job(11, age_min=60), _job(12, age_min=20), _job(13, age_min=12)]
    completed = [_done(100 + i, dur_min=8) for i in range(10)]
    f = _ev(active, [_runner(busy=True)], completed)
    assert len(f) == 1
    f = f[0]
    assert f["kind"] == "not_draining" and f["key"] == KEY
    assert f["depth"] == 3 and f["eta_min"] == 24 and f["oldest_age_min"] == 60
    subj, body = w.render_page(f)
    assert "runner busy" in subj and "queue depth 3" in subj and "ETA ~24 min" in subj
    assert "not draining" in subj
    assert "desktop may be off" not in subj


def test_avg_derived_from_completed_jobs():
    completed = [_done(100 + i, dur_min=20) for i in range(10)]
    # oldest 60: with 8-min fallback ETA=24 -> would page; with real avg 20, ETA=60 -> 60 < 90 no page
    active = [_job(11, age_min=60), _job(12, age_min=30), _job(13, age_min=5)]
    assert _ev(active, [_runner(busy=True)], completed) == []


def test_avg_fallback_8_min_when_no_history():
    assert w.avg_job_minutes([], LBL) == (8.0, "fallback")
    active = [_job(11, age_min=39)]  # ETA 8 + 30 = 38 < 39 -> page
    f = _ev(active, [_runner(busy=True)], [])
    assert f and f[0]["kind"] == "not_draining" and f[0]["avg_source"] == "fallback"


def test_avg_uses_last_10_label_jobs_only_and_ignores_cancelled():
    completed = ([_done(1, dur_min=2, ended_ago=1)]                       # newest
                 + [_done(10 + i, dur_min=10, ended_ago=5 + i) for i in range(10)]
                 + [_done(50, dur_min=1, ended_ago=2, conclusion="cancelled")]
                 + [_done(60, dur_min=99, ended_ago=3, labels=("ubuntu-latest",))])
    avg, src = w.avg_job_minutes(completed, LBL)
    # newest 10 label success/failure jobs: 2 + 9x10 = 92 / 10
    assert src == "last 10 jobs" and avg == pytest.approx(9.2)


# ---------------------------------------------------------------- offline / idle / none

def test_runner_offline_with_queue_pages_offline_wording():
    f = _ev([_job(11, age_min=10), _job(12, age_min=7)], [_runner(status="offline")])
    assert len(f) == 1 and f[0]["kind"] == "runner_offline" and f[0]["key"] == KEY
    subj, body = w.render_page(f[0])
    assert "runner offline (desktop may be off/asleep)" in subj
    assert "11" in body and "12" in body


def test_runner_offline_nothing_queued_no_page():
    assert _ev([], [_runner(status="offline")]) == []


def test_offline_within_grace_no_page():
    # a job queued 2 min ago while the runner reconnects is not yet an outage
    assert _ev([_job(11, age_min=2)], [_runner(status="offline")]) == []


def test_idle_with_queue_stuck_pickup_pages():
    completed = [_done(100, ended_ago=30)]
    f = _ev([_job(11, age_min=20)], [_runner(busy=False)], completed)
    assert len(f) == 1 and f[0]["kind"] == "stuck_pickup"
    subj, _ = w.render_page(f[0])
    assert "runner online but not picking up jobs" in subj


def test_idle_between_jobs_is_not_stuck():
    # runner momentarily idle right after finishing a job (1 min ago): draining, not stuck
    completed = [_done(100, ended_ago=1)]
    assert _ev([_job(11, age_min=40)], [_runner(busy=False)], completed) == []


def test_idle_with_fresh_job_not_stuck():
    assert _ev([_job(11, age_min=1)], [_runner(busy=False)], []) == []


def test_no_runner_registered_pages():
    other = _runner(status="online", name="gzb", labels=("self-hosted", "gzb"))
    f = _ev([_job(11, age_min=10)], [other])
    assert [x["kind"] for x in f] == ["no_runner"]


def test_other_labels_ignored():
    active = [_job(11, labels=("ubuntu-latest",), age_min=500)]
    assert _ev(active, [_runner(status="offline")]) == []


# ---------------------------------------------------------------- two runners, one label

R1, R2 = "cubeasht-orchestrator", "cubeasht-orchestrator-2"


def test_two_runners_both_busy_three_queued_no_page():
    # capacity 2 -> ETA = ceil(3/2) x 8 = 16; oldest 40 < 16 + 30
    active = [_job(10, status="in_progress", started_ago=3), _job(20, status="in_progress", started_ago=5),
              _job(11, age_min=40), _job(12, age_min=25), _job(13, age_min=10)]
    runners = [_runner(name=R1, busy=True), _runner(name=R2, busy=True)]
    assert _ev(active, runners, [_done(100 + i) for i in range(10)]) == []


def test_two_runners_eta_uses_online_busy_count():
    # ceil(3/2) x 8 = 16 + 30 = 46 < 50 -> page; would NOT page with 1-runner ETA 24+30=54
    active = [_job(11, age_min=50), _job(12, age_min=25), _job(13, age_min=10)]
    runners = [_runner(name=R1, busy=True), _runner(name=R2, busy=True)]
    f = _ev(active, runners, [_done(100 + i) for i in range(10)])
    assert len(f) == 1 and f[0]["kind"] == "not_draining" and f[0]["eta_min"] == 16
    _, body = w.render_page(f[0])
    assert "degraded" not in body.lower()


def test_one_offline_one_busy_draining_no_page():
    # capacity 1 -> ETA = 2 x 8 = 16; oldest 30 < 46
    active = [_job(11, age_min=30), _job(12, age_min=10)]
    runners = [_runner(name=R1, status="offline"), _runner(name=R2, busy=True)]
    assert _ev(active, runners, [_done(100 + i) for i in range(10)]) == []


def test_one_offline_one_busy_not_draining_says_degraded():
    active = [_job(11, age_min=60), _job(12, age_min=10)]
    runners = [_runner(name=R1, status="offline"), _runner(name=R2, busy=True)]
    f = _ev(active, runners, [_done(100 + i) for i in range(10)])
    assert len(f) == 1 and f[0]["kind"] == "not_draining" and f[0]["eta_min"] == 16
    subj, body = w.render_page(f[0])
    assert "degraded capacity" in subj and "1/2 runners online" in subj
    assert R1 in body and "offline" in body


def test_two_runners_both_offline_pages_offline():
    runners = [_runner(name=R1, status="offline"), _runner(name=R2, status="offline")]
    f = _ev([_job(11, age_min=20)], runners)
    assert len(f) == 1 and f[0]["kind"] == "runner_offline" and f[0]["key"] == KEY
    assert "runner offline (desktop may be off/asleep)" in w.render_page(f[0])[0]


def test_one_idle_one_busy_with_queue_is_stuck_pickup():
    runners = [_runner(name=R1, busy=False), _runner(name=R2, busy=True)]
    active = [_job(20, status="in_progress", started_ago=12), _job(11, age_min=20)]
    f = _ev(active, runners, [_done(100, ended_ago=30)])
    assert len(f) == 1 and f[0]["kind"] == "stuck_pickup"


# ---------------------------------------------------------------- collapse

def test_collapse_one_finding_listing_all_jobs():
    active = [_job(i, age_min=100 + i) for i in range(1, 6)]
    f = _ev(active, [_runner(status="offline")])
    assert len(f) == 1
    assert [j["id"] for j in f[0]["jobs"]] == [5, 4, 3, 2, 1]  # oldest first
    _, body = w.render_page(f[0])
    for i in range(1, 6):
        assert f"job {i}" in body


# ---------------------------------------------------------------- dedup

def test_dedup_cooldown_and_resolve():
    f = _ev([_job(11, age_min=20)], [_runner(status="offline")])
    to_page, state = w.plan_pages(f, {}, NOW, cooldown_min=60)
    assert len(to_page) == 1
    state = w.mark_paged(state, to_page[0], NOW)
    assert state[KEY]["kind"] == "runner_offline"

    later = NOW + timedelta(minutes=20)
    # same label, even a different kind / more jobs -> still ONE page per window
    f2 = _ev([_job(11, age_min=40), _job(12, age_min=10)], [_runner(busy=False)], [])
    assert f2[0]["key"] == KEY
    assert w.plan_pages(f2, state, later, 60)[0] == []
    assert len(w.plan_pages(f2, state, NOW + timedelta(minutes=61), 60)[0]) == 1

    to_page4, state4 = w.plan_pages([], state, later, 60)
    assert to_page4 == [] and state4 == {}


def test_legacy_v1_state_values_tolerated():
    legacy = {f"queued_job:{REPO}:1": "2026-10-07T11:00:00Z", KEY: "2026-10-07T11:50:00Z"}
    f = _ev([_job(11, age_min=20)], [_runner(status="offline")])
    to_page, st = w.plan_pages(f, legacy, NOW, 60)
    assert to_page == []                       # KEY paged 10 min ago (legacy str value)
    assert list(st) == [KEY]                   # old per-job key cleared


def test_state_roundtrip_and_corrupt(tmp_path):
    p = tmp_path / "sub" / "state.json"
    assert w.load_state(p) == {}
    w.save_state(p, {"k": {"ts": "2026-10-07T12:00:00Z", "kind": "x"}})
    assert w.load_state(p)["k"]["kind"] == "x"
    p.write_text("{not json")
    with pytest.raises(w.WatchdogError):
        w.load_state(p)


# ---------------------------------------------------------------- fail loud

def test_gh_nonzero_raises(monkeypatch):
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, stdout="", stderr="HTTP 401: Bad credentials"))
    with pytest.raises(w.WatchdogError, match="401"):
        w.gh_api("repos/x/y/actions/runners")


def test_gh_unparseable_raises(monkeypatch):
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout="<html>oops", stderr=""))
    with pytest.raises(w.WatchdogError, match="unparseable"):
        w.gh_api("repos/x/y/actions/runners")


def test_gh_missing_key_raises(monkeypatch):
    monkeypatch.setattr(w, "gh_api", lambda path: {"unexpected": 1})
    with pytest.raises(w.WatchdogError):
        w.fetch_runners(REPO)


def test_completed_history_large_total_count_is_not_an_error(monkeypatch):
    def fake(path):
        if "status=completed" in path:
            return {"total_count": 5000, "workflow_runs": [{"id": 7}]}
        return {"total_count": 1, "jobs": [_done(1)]}
    monkeypatch.setattr(w, "gh_api", fake)
    assert [j["id"] for j in w.fetch_recent_completed_jobs(REPO, LBL)] == [1]


def _patch_live(monkeypatch, active, runners, completed=()):
    monkeypatch.setattr(w, "fetch_label_jobs", lambda repo: list(active))
    monkeypatch.setattr(w, "fetch_runners", lambda repo: list(runners))
    monkeypatch.setattr(w, "fetch_recent_completed_jobs", lambda repo, label: list(completed))
    monkeypatch.setattr(w, "_now", lambda: NOW)


def test_main_exits_nonzero_on_gh_failure(monkeypatch, tmp_path, capsys):
    def boom(path):
        raise w.WatchdogError("gh api failed: HTTP 502")
    monkeypatch.setattr(w, "gh_api", boom)
    assert w.main(["--dry-run", "--state-file", str(tmp_path / "s.json")]) != 0
    assert "502" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def test_main_dry_run_touches_nothing(monkeypatch, tmp_path, capsys):
    _patch_live(monkeypatch, [_job(1, age_min=45), _job(2, age_min=40)], [_runner(status="offline")])
    sent = []
    monkeypatch.setattr(w, "_send_page", lambda f: sent.append(f))
    assert w.main(["--dry-run", "--state-file", str(tmp_path / "s.json")]) == 0
    assert sent == [] and not (tmp_path / "s.json").exists()
    out = capsys.readouterr().out
    assert out.count("WOULD PAGE") == 1 and "runner_offline" in out


def test_main_todays_scenario_sends_nothing(monkeypatch, tmp_path):
    active = [_job(10, status="in_progress", started_ago=4, age_min=35),
              _job(11, age_min=31), _job(12, age_min=20), _job(13, age_min=12)]
    _patch_live(monkeypatch, active, [_runner(busy=True)], [_done(100 + i) for i in range(10)])
    sent = []
    monkeypatch.setattr(w, "_send_page", lambda f: sent.append(f) or 1)
    assert w.main(["--state-file", str(tmp_path / "s.json")]) == 0
    assert sent == []


def test_main_live_pages_once_per_label_then_dedups(monkeypatch, tmp_path):
    _patch_live(monkeypatch, [_job(1, age_min=45), _job(2, age_min=44), _job(3, age_min=43)],
                [_runner(status="offline")])
    sent = []
    monkeypatch.setattr(w, "_send_page", lambda f: sent.append(f["key"]) or 123)
    sf = tmp_path / "s.json"
    assert w.main(["--state-file", str(sf)]) == 0
    assert w.main(["--state-file", str(sf)]) == 0
    assert sent == [KEY]
    assert set(json.loads(sf.read_text())) == {KEY}


def test_main_send_failure_exits_nonzero(monkeypatch, tmp_path, capsys):
    _patch_live(monkeypatch, [_job(1, age_min=45)], [_runner(status="offline")])

    def fail(f):
        raise RuntimeError("db down")
    monkeypatch.setattr(w, "_send_page", fail)
    sf = tmp_path / "s.json"
    assert w.main(["--state-file", str(sf)]) != 0
    assert "db down" in capsys.readouterr().err
    assert not sf.exists() or json.loads(sf.read_text()) == {}
