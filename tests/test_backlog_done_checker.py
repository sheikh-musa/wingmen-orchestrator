"""Unit tests for scripts/backlog_done_checker.py.

Everything is mocked: no real DB, no real HTTP, no real `gh`. `_dsn()`/`main()`
are deliberately never called (they would fall back to the repo's real .env),
so `check_once` and the signal evaluators are exercised with a fake connection
passed in directly, exactly as the production functions take it.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from scripts import backlog_done_checker as bdc


# ── helpers ───────────────────────────────────────────────────────────────────
def _fake_response(status: int) -> MagicMock:
    """A urlopen() return value usable as a context manager."""
    resp = MagicMock()
    resp.status = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def _fake_conn(rows=None):
    """A psycopg connection mock whose single cursor is also a context manager.

    The same cursor is returned for every `with conn.cursor() as cur` block, so
    tests can inspect every execute() call made during check_once.
    """
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    cur.fetchall.return_value = rows or []
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _updates(cur):
    return [c for c in cur.execute.call_args_list if "UPDATE operator_backlog" in c.args[0]]


# ── _sig_deploy_live ─────────────────────────────────────────────────────────
def test_deploy_live_met_on_matching_status(monkeypatch):
    monkeypatch.setattr(bdc.urllib.request, "urlopen", lambda *a, **kw: _fake_response(200))
    met, evidence = bdc._sig_deploy_live({"type": "deploy_live", "url": "https://example.test/health"})
    assert met is True
    assert "-> 200" in evidence


def test_deploy_live_not_met_on_status_mismatch(monkeypatch):
    monkeypatch.setattr(bdc.urllib.request, "urlopen", lambda *a, **kw: _fake_response(200))
    met, evidence = bdc._sig_deploy_live(
        {"type": "deploy_live", "url": "https://example.test/health", "expect_status": 204}
    )
    assert met is False
    assert "want 204" in evidence


def test_deploy_live_not_met_and_does_not_raise_when_get_errors(monkeypatch):
    def _boom(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(bdc.urllib.request, "urlopen", _boom)
    met, evidence = bdc._sig_deploy_live({"type": "deploy_live", "url": "https://example.test/health"})
    assert met is False
    assert "error OSError" in evidence


# ── _sig_pr_merged ───────────────────────────────────────────────────────────
def test_pr_merged_met_when_merged_with_merged_at(monkeypatch):
    stdout = json.dumps({"state": "MERGED", "mergedAt": "2026-10-01T12:00:00Z"})
    monkeypatch.setattr(bdc.subprocess, "run", lambda *a, **kw: _completed(stdout=stdout))
    met, evidence = bdc._sig_pr_merged({"type": "pr_merged", "repo": "owner/repo", "pr": 7})
    assert met is True
    assert "owner/repo#7" in evidence


def test_pr_merged_not_met_when_open(monkeypatch):
    stdout = json.dumps({"state": "OPEN", "mergedAt": None})
    monkeypatch.setattr(bdc.subprocess, "run", lambda *a, **kw: _completed(stdout=stdout))
    met, _ = bdc._sig_pr_merged({"type": "pr_merged", "repo": "owner/repo", "pr": 7})
    assert met is False


def test_pr_merged_not_met_when_merged_state_has_no_merged_at(monkeypatch):
    stdout = json.dumps({"state": "MERGED", "mergedAt": None})
    monkeypatch.setattr(bdc.subprocess, "run", lambda *a, **kw: _completed(stdout=stdout))
    met, _ = bdc._sig_pr_merged({"type": "pr_merged", "repo": "owner/repo", "pr": 7})
    assert met is False


def test_pr_merged_not_met_on_nonzero_gh_exit(monkeypatch):
    monkeypatch.setattr(bdc.subprocess, "run", lambda *a, **kw: _completed(returncode=1))
    met, evidence = bdc._sig_pr_merged({"type": "pr_merged", "repo": "owner/repo", "pr": 7})
    assert met is False
    assert "rc=1" in evidence


def test_pr_merged_not_met_and_does_not_raise_when_gh_errors(monkeypatch):
    def _boom(*a, **kw):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(bdc.subprocess, "run", _boom)
    met, evidence = bdc._sig_pr_merged({"type": "pr_merged", "repo": "owner/repo", "pr": 7})
    assert met is False
    assert "error FileNotFoundError" in evidence


# ── _sig_deploy_stage ────────────────────────────────────────────────────────
def test_deploy_stage_met_when_latest_stage_is_live():
    conn, cur = _fake_conn()
    cur.fetchone.return_value = ("live",)
    met, evidence = bdc._sig_deploy_stage(conn, {"type": "deploy_stage", "workstream": "storefront"})
    assert met is True
    assert "stage=live" in evidence


@pytest.mark.parametrize("stage", ["staging", "building", "failed", None])
def test_deploy_stage_not_met_for_other_stage_or_no_row(stage):
    conn, cur = _fake_conn()
    cur.fetchone.return_value = (stage,) if stage is not None else None
    met, _ = bdc._sig_deploy_stage(conn, {"type": "deploy_stage", "workstream": "storefront"})
    assert met is False


def test_deploy_stage_not_met_and_does_not_raise_when_query_errors():
    conn = MagicMock()
    conn.cursor.side_effect = RuntimeError("db down")
    met, evidence = bdc._sig_deploy_stage(conn, {"type": "deploy_stage", "workstream": "storefront"})
    assert met is False
    assert "error RuntimeError" in evidence


# ── _evaluate dispatch ───────────────────────────────────────────────────────
@pytest.mark.parametrize("sig", [
    {"type": "manual"},
    {"type": "not_a_real_type"},
    {},  # missing type
])
def test_evaluate_unknown_or_manual_type_is_never_met_and_runs_no_evaluator(monkeypatch, sig):
    def _must_not_run(*a, **kw):
        raise AssertionError(f"evaluator must not run for type={sig.get('type')!r}")

    for name in ("_sig_deploy_live", "_sig_pr_merged", "_sig_deploy_stage"):
        monkeypatch.setattr(bdc, name, _must_not_run)

    met, evidence = bdc._evaluate(MagicMock(), sig)
    assert met is False
    assert "unsupported/manual" in evidence


def test_evaluate_dispatches_deploy_live(monkeypatch):
    monkeypatch.setattr(bdc, "_sig_deploy_live", lambda sig: (True, "live-ok"))
    conn = MagicMock()
    assert bdc._evaluate(conn, {"type": "deploy_live"}) == (True, "live-ok")
    conn.cursor.assert_not_called()


def test_evaluate_dispatches_pr_merged(monkeypatch):
    monkeypatch.setattr(bdc, "_sig_pr_merged", lambda sig: (True, "merged-ok"))
    assert bdc._evaluate(MagicMock(), {"type": "pr_merged"}) == (True, "merged-ok")


def test_evaluate_dispatches_deploy_stage(monkeypatch):
    seen = {}

    def _deploy_stage(conn, sig):
        seen["conn"] = conn
        return True, "staged-ok"

    monkeypatch.setattr(bdc, "_sig_deploy_stage", _deploy_stage)
    conn = MagicMock()
    assert bdc._evaluate(conn, {"type": "deploy_stage"}) == (True, "staged-ok")
    assert seen["conn"] is conn


# ── check_once ───────────────────────────────────────────────────────────────
def _patch_evaluators(monkeypatch, deploy_live_met, pr_merged_met):
    calls = {"deploy_live": 0, "pr_merged": 0}
    evaluated = []
    real_evaluate = bdc._evaluate

    def _deploy_live(sig):
        calls["deploy_live"] += 1
        return deploy_live_met, "evidence-live"

    def _pr_merged(sig):
        calls["pr_merged"] += 1
        return pr_merged_met, "evidence-pr"

    def _spy_evaluate(conn, sig):
        evaluated.append(sig.get("type"))
        return real_evaluate(conn, sig)

    monkeypatch.setattr(bdc, "_sig_deploy_live", _deploy_live)
    monkeypatch.setattr(bdc, "_sig_pr_merged", _pr_merged)
    monkeypatch.setattr(bdc, "_evaluate", _spy_evaluate)
    return calls, evaluated


_ROWS = [
    (101, "met deploy live", {"type": "deploy_live", "url": "https://example.test/health"}),
    (102, "unmet pr merged", {"type": "pr_merged", "repo": "owner/repo", "pr": 7}),
    (103, "manual item", {"type": "manual"}),
]


def test_check_once_updates_only_met_non_manual_rows(monkeypatch):
    calls, evaluated = _patch_evaluators(monkeypatch, deploy_live_met=True, pr_merged_met=False)
    conn, cur = _fake_conn(rows=_ROWS)

    completed = bdc.check_once(conn, dry_run=False)

    assert completed == 1
    assert calls == {"deploy_live": 1, "pr_merged": 1}, "manual row must be skipped before evaluation"
    assert evaluated == ["deploy_live", "pr_merged"], "manual row must never reach _evaluate"

    updates = _updates(cur)
    assert len(updates) == 1
    sql, params = updates[0].args[0], updates[0].args[1]
    assert "status='done'" in sql
    assert params[1] == 101, "only the met row may be updated"
    assert params[0].startswith(" [auto-done")
    assert "evidence-live" in params[0]
    conn.commit.assert_called_once()


def test_check_once_dry_run_issues_no_update_and_commits_nothing(monkeypatch):
    calls, evaluated = _patch_evaluators(monkeypatch, deploy_live_met=True, pr_merged_met=False)
    conn, cur = _fake_conn(rows=_ROWS)

    completed = bdc.check_once(conn, dry_run=True)

    # check_once counts met signals even in dry-run; it just never writes them.
    assert completed == 1
    assert calls == {"deploy_live": 1, "pr_merged": 1}
    assert evaluated == ["deploy_live", "pr_merged"]
    assert _updates(cur) == []
    conn.commit.assert_not_called()


def test_check_once_unmet_only_returns_zero_and_writes_nothing(monkeypatch):
    _patch_evaluators(monkeypatch, deploy_live_met=False, pr_merged_met=False)
    conn, cur = _fake_conn(rows=_ROWS)

    assert bdc.check_once(conn, dry_run=False) == 0
    assert _updates(cur) == []
    conn.commit.assert_not_called()
