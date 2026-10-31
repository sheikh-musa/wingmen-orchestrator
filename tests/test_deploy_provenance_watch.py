"""deploy_provenance_watch: CI-only drift must not raise PROD_SHA_DRIFT.

No network, no DB: Vercel + gh calls are mocked.
"""
import json
import subprocess

import pytest

from scripts import deploy_provenance_watch as dpw

LIVE = "deadbeef0000000000000000000000000000beef"
HEAD = "abc1230000000000000000000000000000000123"


def _project(app):
    return {"link": {"org": "acme", "repo": "app", "productionBranch": "main"},
            "targets": {"production": {"meta": {"githubCommitSha": LIVE},
                                       "readyState": "READY", "id": "dpl_1", "url": "u",
                                       "creator": {"username": "x"}}}}


def _fake_run(compare_result):
    """compare_result: list of files, or None to simulate a failing compare call."""
    def run(cmd, **kw):
        path = cmd[2]
        if "/commits/" in path:
            return subprocess.CompletedProcess(cmd, 0, HEAD + "\n", "")
        if "/compare/" in path:
            assert path.endswith("%s...%s" % (LIVE, HEAD))
            if compare_result is None:
                return subprocess.CompletedProcess(cmd, 1, "", "HTTP 502")
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"status": "ahead", "files": compare_result}), "")
        raise AssertionError("unexpected gh call %r" % (cmd,))
    return run


@pytest.fixture
def collect_with(monkeypatch):
    monkeypatch.setattr(dpw, "SILOS", ["silo-a"])
    monkeypatch.setattr(dpw, "_vercel", lambda path: _project(path))

    def go(compare_result):
        monkeypatch.setattr(dpw.subprocess, "run", _fake_run(compare_result))
        return dpw.collect()[0]
    return go


def test_ci_only_change_no_drift_alert(collect_with):
    r = collect_with([".github/workflows/x.yml"])
    assert r["alerts"] == []
    assert [i["code"] for i in r["info"]] == ["CI_ONLY_DRIFT"]


def test_github_plus_app_file_alerts(collect_with):
    r = collect_with([".github/workflows/x.yml", "src/app/page.tsx"])
    assert [a["code"] for a in r["alerts"]] == ["PROD_SHA_DRIFT"]
    assert "could not be read" not in r["alerts"][0]["detail"]
    assert r["info"] == []


def test_file_list_unavailable_alerts_fail_safe(collect_with):
    r = collect_with(None)
    assert [a["code"] for a in r["alerts"]] == ["PROD_SHA_DRIFT"]
    assert "could not be read" in r["alerts"][0]["detail"]


def test_compare_not_ahead_or_truncated_is_unreadable(monkeypatch):
    def run_with(payload):
        monkeypatch.setattr(dpw.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, json.dumps(payload), ""))
        return dpw._github_changed_files("o", "r", LIVE, HEAD)
    assert run_with({"status": "diverged", "files": [".github/x.yml"]}) is None
    assert run_with({"status": "ahead", "files": [".github/x"] * dpw.COMPARE_FILE_CAP}) is None
    assert run_with({"status": "ahead", "files": []}) is None
    assert run_with({"status": "ahead", "files": [".github/x.yml"]}) == [".github/x.yml"]


def test_existing_drift_still_alerts():
    # Pre-existing behaviour: plain drift with no file info still alerts.
    codes = [a["code"] for a in dpw.evaluate("s", "deadbeef", "READY", "abc123")]
    assert codes == ["PROD_SHA_DRIFT"]
    assert dpw.evaluate("s", "abc123def", "READY", "abc123def") == []


def test_selftest_passes():
    with pytest.raises(SystemExit) as e:
        dpw._selftest()
    assert e.value.code == 0
