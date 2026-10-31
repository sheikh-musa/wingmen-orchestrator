"""Tests for scripts/lib/merge_signoff.py (op#36818, cc-substrate 87772).

The gate is only worth having if each of its refusals fires, so almost every case
here is a REFUSAL: a PASS that names the wrong sha, a stale PASS, an author's own
PASS, a PASS from an agent that is not a registered auditor, and a lookup that
cannot run at all. One happy path proves it can say yes and prints the evidence.

The DB is stubbed at one seam (connect()), never with a real substrate connection:
the point under test is the matching logic, and a test that needs production to run
is a test that stops running.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import merge_signoff as ms  # noqa: E402

HEAD = "8ea32ef" + "0" * 33            # 40 chars, sha-shaped
OTHER = "deadbee" + "1" * 33
REPO_N = "wingmen/orchestrator"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
AFTER = NOW - timedelta(hours=1)       # the head commit, an hour before the PASS


class _Cur:
    def __init__(self, rows, families, bases):
        self.rows = rows
        self.families = families
        self.bases = bases
        self._result = []

    def execute(self, sql, params=None):
        if "protected_agents" in sql:
            self._result = [(f,) for f in self.families]
        elif "agent_status" in sql:
            agent = (params or [None])[0]
            base = self.bases.get(agent)
            self._result = [(base,)] if base else []
        elif "agent_messages" in sql:
            self._result = self.rows
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _row(row_id, from_agent, body, when=NOW, is_test=False, subject=""):
    return (row_id, from_agent, when, body, subject, is_test)


def _pass_body(sha=HEAD, pr=765, repo="orchestrator"):
    return f"VERDICT: PASS | {repo} | PR#{pr} | {sha}\nsmall fix, re-request when green"


def _install(monkeypatch, rows, bases, families=("cc-quality", "cc-finance")):
    monkeypatch.setattr(ms, "connect", lambda dsn: _Conn(_Cur(rows, families, bases)))


def _run(monkeypatch, rows, bases, branch="cc-substrate/thing", **kw):
    _install(monkeypatch, rows, bases)
    argv = ["--repo", REPO_N, "--pr", "765", "--head", HEAD, "--branch", branch,
            "--commit-date", AFTER.isoformat()]
    return ms.main(argv)


def test_independent_signoff_on_the_full_sha_merges_and_prints_evidence(monkeypatch, capsys):
    rc = _run(monkeypatch, [_row(87516, "cc-quality", _pass_body())],
              {"cc-quality": "cc-quality"})
    out = capsys.readouterr().out
    assert rc == 0
    assert "sign-off: PASS id=87516 by cc-quality" in out
    assert HEAD[:12] in out


def test_a_pass_naming_a_prefix_instead_of_the_full_sha_is_refused(monkeypatch, capsys):
    """The stale-review hole: 12 chars match yesterday's head as well as today's."""
    rc = _run(monkeypatch, [_row(1, "cc-quality", _pass_body(sha=HEAD[:12]))],
              {"cc-quality": "cc-quality"})
    err = capsys.readouterr().err
    assert rc == 2
    assert "FULL" in err and "40-char sha" in err


def test_a_pass_for_another_sha_is_refused(monkeypatch, capsys):
    rc = _run(monkeypatch, [_row(1, "cc-quality", _pass_body(sha=OTHER))],
              {"cc-quality": "cc-quality"})
    assert rc == 2
    assert "no VERDICT: PASS" in capsys.readouterr().err


def test_a_pass_older_than_the_head_commit_is_stale_and_refused(monkeypatch, capsys):
    stale = AFTER - timedelta(minutes=5)
    rc = _run(monkeypatch, [_row(1, "cc-quality", _pass_body(), when=stale)],
              {"cc-quality": "cc-quality"})
    assert rc == 2
    assert "no VERDICT: PASS" in capsys.readouterr().err


def test_the_authors_own_pass_is_refused(monkeypatch, capsys):
    """An auditor reviewing its own branch is still self-review, and the check must
    fire BEFORE the auditor bar would have accepted it."""
    rc = _run(monkeypatch, [_row(1, "cc-quality-2", _pass_body())],
              {"cc-quality-2": "cc-quality"}, branch="cc-quality/some-fix")
    assert rc == 2
    assert "author" in capsys.readouterr().err


def test_a_pass_from_a_non_auditor_is_refused(monkeypatch, capsys):
    rc = _run(monkeypatch, [_row(1, "cc-shipforge", _pass_body())],
              {"cc-shipforge": "cc-shipforge"})
    err = capsys.readouterr().err
    assert rc == 2
    assert "not an auditor" in err


def test_a_pass_for_the_right_sha_but_another_pr_number_is_refused(monkeypatch, capsys):
    rc = _run(monkeypatch, [_row(1, "cc-quality", _pass_body(pr=764))],
              {"cc-quality": "cc-quality"})
    assert rc == 2
    assert "no VERDICT: PASS" in capsys.readouterr().err


def test_a_pass_for_another_repo_is_refused(monkeypatch, capsys):
    body = _pass_body().replace("orchestrator", "cosem-platform")
    rc = _run(monkeypatch, [_row(1, "cc-quality", body)], {"cc-quality": "cc-quality"})
    assert rc == 2


def test_a_test_row_is_not_a_signoff(monkeypatch, capsys):
    rc = _run(monkeypatch, [_row(1, "cc-quality", _pass_body(), is_test=True)],
              {"cc-quality": "cc-quality"})
    assert rc == 2


def test_two_different_reviewer_families_on_the_same_sha_refuse(monkeypatch, capsys):
    rc = _run(monkeypatch,
              [_row(1, "cc-quality", _pass_body()), _row(2, "cc-finance", _pass_body())],
              {"cc-quality": "cc-quality", "cc-finance": "cc-finance"})
    err = capsys.readouterr().err
    assert rc == 2
    assert "two different reviewer families" in err


def test_the_same_reviewer_family_twice_is_fine(monkeypatch, capsys):
    rc = _run(monkeypatch,
              [_row(1, "cc-quality", _pass_body()), _row(2, "cc-quality-2", _pass_body())],
              {"cc-quality": "cc-quality", "cc-quality-2": "cc-quality"})
    assert rc == 0, "an instance of the same auditor family is not an ambiguity"


def test_lookup_failure_fails_closed(monkeypatch, capsys):
    def boom(dsn):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(ms, "connect", boom)
    rc = ms.main(["--repo", REPO_N, "--pr", "765", "--head", HEAD,
                  "--commit-date", AFTER.isoformat()])
    assert rc == 2
    assert "fails closed" in capsys.readouterr().err


def test_no_registered_auditors_fails_closed(monkeypatch, capsys):
    _install(monkeypatch, [_row(1, "cc-quality", _pass_body())],
             {"cc-quality": "cc-quality"}, families=())
    rc = ms.main(["--repo", REPO_N, "--pr", "765", "--head", HEAD,
                  "--commit-date", AFTER.isoformat()])
    assert rc == 2
    assert "auditor-singleton" in capsys.readouterr().err


def test_a_short_head_argument_is_refused_before_any_lookup(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(ms, "connect", lambda dsn: called.append(dsn))
    rc = ms.main(["--repo", REPO_N, "--pr", "765", "--head", HEAD[:12],
                  "--commit-date", AFTER.isoformat()])
    assert rc == 2
    assert called == [], "nothing may be looked up for a non-full sha"


def test_pr_number_matching_is_number_bounded():
    assert ms.names_pr("PR#765", 765)
    assert ms.names_pr("fixed in #765", 765)
    assert ms.names_pr("PR 765 green", 765)
    assert not ms.names_pr("PR#7650", 765)
    assert not ms.names_pr("issue #1765", 765)
