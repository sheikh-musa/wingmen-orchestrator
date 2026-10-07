"""Per-job state-file isolation for scripts/ci_bootstrap_schema.py (bus #58575).

Regression: a FIXED shared state file ( /tmp/wingmen-ci-bootstrap-schema-state.json )
let two concurrent self-hosted CI jobs clobber each other — job B's `up` overwrote the
file, and job A's `down` then read B's entry and rmtree'd B's LIVE Postgres socket dir
out from under B's still-running tests ("connection to server on socket ... No such file
or directory" mid-run). Fix = per-JOB state path ($RUNNER_TEMP) + an owner stamp that
`down` refuses to act across.

These are pure-logic tests: tear_down_cluster is monkeypatched, so NO real Postgres is
needed (they run in the PYTEST_NO_DB unit lane too).
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import ci_bootstrap_schema as cbs  # noqa: E402

_JOB_ENV = ("RUNNER_NAME", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")


@pytest.fixture
def clean_job_env(monkeypatch):
    for k in _JOB_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _set_job(monkeypatch, runner, run_id, attempt):
    monkeypatch.setenv("RUNNER_NAME", runner)
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)


def _write_state(path: Path, *, owner, datadir="/tmp/dd", sockdir="/tmp/sd"):
    path.write_text(json.dumps(
        {"dsn": "x", "datadir": datadir, "sockdir": sockdir, "port": "1", "pg_bin": "/pg", "owner": owner}
    ))


# ── _job_owner_id ────────────────────────────────────────────────────────────

def test_owner_id_joins_job_env(clean_job_env):
    _set_job(clean_job_env, "cubeasht-orchestrator-2", "123", "1")
    assert cbs._job_owner_id() == "cubeasht-orchestrator-2:123:1"


def test_owner_id_empty_outside_actions(clean_job_env):
    assert cbs._job_owner_id() == "::"  # manual/local -> guard is a no-op


# ── cmd_down owner guard (THE regression prevention) ──────────────────────────

def test_down_refuses_other_jobs_cluster(clean_job_env, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cbs, "tear_down_cluster", lambda *a, **k: calls.append(a))
    sf = tmp_path / "state.json"
    _write_state(sf, owner="cubeasht-orchestrator:100:1", sockdir="/tmp/sock-A")
    # THIS job is a DIFFERENT runner — must NOT tear down job A's live cluster.
    _set_job(monkeypatch, "cubeasht-orchestrator-2", "200", "1")
    rc = cbs.cmd_down(sf)
    assert rc == 0, "refusal must not fail an otherwise-green job"
    assert calls == [], "must NOT tear down another job's cluster (bus #58575 clobber)"
    assert sf.exists(), "the other job's state file must be left for its own `down`"


def test_down_tears_down_own_cluster(clean_job_env, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cbs, "tear_down_cluster", lambda *a, **k: calls.append(a))
    sf = tmp_path / "state.json"
    _set_job(monkeypatch, "cubeasht-orchestrator-2", "200", "1")
    _write_state(sf, owner=cbs._job_owner_id(), datadir="/tmp/dd-mine", sockdir="/tmp/sd-mine")
    rc = cbs.cmd_down(sf)
    assert rc == 0
    assert calls and calls[0][0] == "/tmp/dd-mine" and calls[0][1] == "/tmp/sd-mine"
    assert not sf.exists(), "own state file is unlinked after teardown"


def test_down_legacy_state_without_owner_still_tears_down(clean_job_env, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cbs, "tear_down_cluster", lambda *a, **k: calls.append(a))
    sf = tmp_path / "state.json"
    sf.write_text(json.dumps({"dsn": "x", "datadir": "/tmp/dd", "sockdir": "/tmp/sd", "pg_bin": "/pg"}))
    _set_job(monkeypatch, "r", "1", "1")
    rc = cbs.cmd_down(sf)
    assert rc == 0 and calls, "a legacy ownerless state file stays tearable (back-compat)"


def test_down_missing_state_is_noop(tmp_path):
    assert cbs.cmd_down(tmp_path / "nope.json") == 0


# ── default state path is per-job under $RUNNER_TEMP ──────────────────────────

def test_default_state_file_under_runner_temp(monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", "/tmp/runner-xyz/_temp")
    reloaded = importlib.reload(cbs)
    try:
        assert str(reloaded.DEFAULT_STATE_FILE) == "/tmp/runner-xyz/_temp/wingmen-ci-bootstrap-schema-state.json"
    finally:
        monkeypatch.delenv("RUNNER_TEMP", raising=False)
        importlib.reload(cbs)
