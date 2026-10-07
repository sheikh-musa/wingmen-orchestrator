"""Tests for scripts/ci_queue_watchdog.py — no network, no DB.

Covers (cc-fleet-health, orch-console bus #57952):
  * queued cubeasht job under / over threshold
  * queued job on OTHER labels ignored
  * runner offline + queued cubeasht job -> page
  * runner offline + nothing queued -> no page (desktop off overnight is expected)
  * dedup cooldown + clear-on-resolve
  * gh failure / unparseable output -> raises, main() exits non-zero
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


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(jid=1, status="queued", labels=("self-hosted", "cubeasht"), age_min=45, name="Lint & Test"):
    created = NOW - timedelta(minutes=age_min)
    return {
        "id": jid, "run_id": 900, "name": name, "status": status,
        "labels": list(labels), "created_at": _iso(created),
        # GitHub fills started_at with a placeholder on queued jobs; must NOT be used.
        "started_at": _iso(NOW),
        "html_url": f"https://github.com/{REPO}/actions/runs/900/job/{jid}",
    }


def _runner(status="online", labels=("self-hosted", "Linux", "X64", "cubeasht", "win-wsl"),
            name="cubeasht-orchestrator"):
    return {"name": name, "status": status, "busy": False, "labels": [{"name": l} for l in labels]}


# ---------- pure classification ----------

def test_queued_job_over_threshold_is_finding():
    f = w.classify_jobs(REPO, [_job(age_min=45)], "cubeasht", 30, NOW)
    assert len(f) == 1
    assert f[0]["kind"] == "queued_job"
    assert f[0]["key"] == f"queued_job:{REPO}:1"
    assert f[0]["age_min"] == 45


def test_queued_job_under_threshold_ignored():
    assert w.classify_jobs(REPO, [_job(age_min=10)], "cubeasht", 30, NOW) == []


def test_age_uses_created_at_not_started_at():
    # started_at == NOW (placeholder) would give age 0; created_at gives 45.
    f = w.classify_jobs(REPO, [_job(age_min=45)], "cubeasht", 30, NOW)
    assert f and f[0]["age_min"] == 45


def test_queued_job_other_labels_ignored():
    jobs = [_job(labels=("ubuntu-latest",), age_min=120)]
    assert w.classify_jobs(REPO, jobs, "cubeasht", 30, NOW) == []


def test_non_queued_job_ignored():
    jobs = [_job(status="in_progress", age_min=120), _job(jid=2, status="completed", age_min=120)]
    assert w.classify_jobs(REPO, jobs, "cubeasht", 30, NOW) == []


def test_queued_jobs_listing_any_age():
    jobs = [_job(jid=1, age_min=2), _job(jid=2, labels=("ubuntu-latest",), age_min=99)]
    assert [j["id"] for j in w.queued_label_jobs(jobs, "cubeasht")] == [1]


def test_runner_offline_with_queued_job_pages():
    f = w.classify_runners(REPO, [_runner(status="offline")], "cubeasht", queued_count=1)
    assert len(f) == 1
    assert f[0]["kind"] == "runner_offline"
    assert f[0]["key"] == f"runner_offline:{REPO}:cubeasht-orchestrator"


def test_runner_offline_nothing_queued_no_page():
    assert w.classify_runners(REPO, [_runner(status="offline")], "cubeasht", queued_count=0) == []


def test_runner_online_with_queued_no_runner_finding():
    assert w.classify_runners(REPO, [_runner()], "cubeasht", queued_count=3) == []


def test_runner_of_other_label_ignored():
    r = _runner(status="offline", labels=("self-hosted", "gzb"), name="gzb-runner")
    # no runner carries the label at all + queued -> no_runner finding (not runner_offline for gzb)
    f = w.classify_runners(REPO, [r], "cubeasht", queued_count=1)
    assert [x["kind"] for x in f] == ["no_runner"]


def test_evaluate_offline_runner_plus_old_job_gives_both():
    f = w.evaluate(REPO, [_job(age_min=45)], [_runner(status="offline")], "cubeasht", 30, NOW)
    assert sorted(x["kind"] for x in f) == ["queued_job", "runner_offline"]


def test_evaluate_offline_runner_nothing_queued_is_quiet():
    assert w.evaluate(REPO, [], [_runner(status="offline")], "cubeasht", 30, NOW) == []


# ---------- dedup ----------

def test_dedup_cooldown_and_resolve():
    f = w.classify_jobs(REPO, [_job(age_min=45)], "cubeasht", 30, NOW)
    to_page, state = w.plan_pages(f, {}, NOW, cooldown_min=60)
    assert len(to_page) == 1
    state = w.mark_paged(state, to_page[0]["key"], NOW)

    # 20 min later: still within cooldown -> suppressed
    later = NOW + timedelta(minutes=20)
    to_page2, state2 = w.plan_pages(f, state, later, cooldown_min=60)
    assert to_page2 == []
    assert to_page[0]["key"] in state2

    # 61 min later: cooldown expired -> re-page
    to_page3, _ = w.plan_pages(f, state, NOW + timedelta(minutes=61), cooldown_min=60)
    assert len(to_page3) == 1

    # finding resolved -> key cleared
    to_page4, state4 = w.plan_pages([], state, later, cooldown_min=60)
    assert to_page4 == [] and state4 == {}


def test_state_roundtrip(tmp_path):
    p = tmp_path / "sub" / "state.json"
    assert w.load_state(p) == {}
    w.save_state(p, {"k": "2026-10-07T12:00:00Z"})
    assert w.load_state(p) == {"k": "2026-10-07T12:00:00Z"}


def test_corrupt_state_fails_loud(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    with pytest.raises(w.WatchdogError):
        w.load_state(p)


# ---------- gh failure fails loud ----------

def test_gh_nonzero_raises(monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0], 1, stdout="", stderr="HTTP 401: Bad credentials")
    monkeypatch.setattr(w.subprocess, "run", fake_run)
    with pytest.raises(w.WatchdogError, match="401"):
        w.gh_api("repos/x/y/actions/runners")


def test_gh_unparseable_raises(monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess(a[0], 0, stdout="<html>oops", stderr="")
    monkeypatch.setattr(w.subprocess, "run", fake_run)
    with pytest.raises(w.WatchdogError, match="unparseable"):
        w.gh_api("repos/x/y/actions/runners")


def test_gh_missing_key_raises(monkeypatch):
    monkeypatch.setattr(w, "gh_api", lambda path: {"unexpected": 1})
    with pytest.raises(w.WatchdogError):
        w.fetch_runners(REPO)


def test_main_exits_nonzero_on_gh_failure(monkeypatch, tmp_path, capsys):
    def boom(path):
        raise w.WatchdogError("gh api failed: HTTP 502")
    monkeypatch.setattr(w, "gh_api", boom)
    rc = w.main(["--dry-run", "--state-file", str(tmp_path / "s.json")])
    assert rc != 0
    assert "502" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def test_main_dry_run_touches_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(w, "fetch_label_jobs", lambda repo: [_job(age_min=45)])
    monkeypatch.setattr(w, "fetch_runners", lambda repo: [_runner(status="offline")])
    monkeypatch.setattr(w, "_now", lambda: NOW)
    sent = []
    monkeypatch.setattr(w, "_send_page", lambda f: sent.append(f))
    rc = w.main(["--dry-run", "--state-file", str(tmp_path / "s.json")])
    assert rc == 0
    assert sent == []
    assert not (tmp_path / "s.json").exists()
    out = capsys.readouterr().out
    assert "WOULD PAGE" in out and "queued_job" in out and "runner_offline" in out


def test_main_live_pages_once_then_dedups(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "fetch_label_jobs", lambda repo: [_job(age_min=45)])
    monkeypatch.setattr(w, "fetch_runners", lambda repo: [_runner()])
    monkeypatch.setattr(w, "_now", lambda: NOW)
    sent = []
    monkeypatch.setattr(w, "_send_page", lambda f: sent.append(f["key"]) or 123)
    sf = tmp_path / "s.json"
    assert w.main(["--state-file", str(sf)]) == 0
    assert w.main(["--state-file", str(sf)]) == 0
    assert sent == [f"queued_job:{REPO}:1"]
    assert json.loads(sf.read_text()).keys() == {f"queued_job:{REPO}:1"}


def test_main_send_failure_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(w, "fetch_label_jobs", lambda repo: [_job(age_min=45)])
    monkeypatch.setattr(w, "fetch_runners", lambda repo: [_runner()])
    monkeypatch.setattr(w, "_now", lambda: NOW)

    def fail(f):
        raise RuntimeError("db down")
    monkeypatch.setattr(w, "_send_page", fail)
    sf = tmp_path / "s.json"
    rc = w.main(["--state-file", str(sf)])
    assert rc != 0
    assert "db down" in capsys.readouterr().err
    # nothing recorded as paged -> next run retries
    assert not sf.exists() or json.loads(sf.read_text()) == {}


def test_page_text_is_plain_language():
    f = w.classify_jobs(REPO, [_job(age_min=45)], "cubeasht", 30, NOW)[0]
    subj, body = w.render_page(f)
    assert "wingmen-orchestrator" in subj and "45" in subj and "Lint & Test" in subj
    assert "\n" not in subj
    assert "desktop" in body.lower() and len(body) >= 40
