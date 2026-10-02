"""reset_audit_row — the pre-clear audit row reset_auditor.sh writes BEFORE any keystroke.

Exit code is the contract: 0 only after the row COMMITS; non-zero on any failure, so the
caller aborts with the body untouched (dead-man's switch). DSN is read FILE-FIRST
(substrate_dsn.dsn_from_env_file) — a long-lived caller's inherited DATABASE_URL may hold a
pre-rotation password, and a stale-password connect extends the pooler circuit-breaker
(op#24342).
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "lib"))
import reset_audit_row as rar  # noqa: E402


class _FakeCur:
    def __init__(self, log): self.log = log
    def execute(self, sql, params=None): self.log.append((sql, params))
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, log): self.log = log; self.committed = False
    def cursor(self): return _FakeCur(self.log)
    def commit(self): self.committed = True
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_writes_self_addressed_audit_row_and_commits(monkeypatch):
    log, conns = [], []
    monkeypatch.setattr(rar, "dsn_from_env_file", lambda: "postgres://file-first")
    def fake_connect(dsn):
        assert dsn == "postgres://file-first", "must use the FILE-first DSN"
        c = _FakeConn(log); conns.append(c); return c
    monkeypatch.setattr(rar, "_connect", fake_connect)
    rc = rar.main(["--by", "cc-fleet-health", "--base", "cc-storefront", "--session", "storefront",
                   "--handoff", "/abs/h.md", "--reason", "bloat 832k"])
    assert rc == 0 and conns[0].committed
    sql, params = [x for x in log if "INSERT" in x[0]][0]
    assert "agent_messages" in sql
    assert params[0] == "cc-fleet-health" and params[1] == "cc-fleet-health"  # self-addressed
    assert "cc-storefront" in params[2] and "/abs/h.md" in params[3] and "bloat 832k" in params[3]


def test_db_failure_is_nonzero(monkeypatch):
    monkeypatch.setattr(rar, "dsn_from_env_file", lambda: "postgres://x")
    def boom(dsn): raise RuntimeError("pooler down")
    monkeypatch.setattr(rar, "_connect", boom)
    assert rar.main(["--by", "cc-fleet-health", "--base", "cc-quality", "--session", "quality",
                     "--handoff", "/h", "--reason", "r"]) != 0


def test_missing_dsn_is_nonzero(monkeypatch):
    def nodsn(): raise RuntimeError("no DATABASE_URL")
    monkeypatch.setattr(rar, "dsn_from_env_file", nodsn)
    assert rar.main(["--by", "cc-fleet-health", "--base", "cc-quality", "--session", "quality",
                     "--handoff", "/h", "--reason", "r"]) != 0


def _capture(monkeypatch):
    log = []
    monkeypatch.setattr(rar, "dsn_from_env_file", lambda: "postgres://f")
    monkeypatch.setattr(rar, "_connect", lambda dsn: _FakeConn(log))
    return log


def test_forced_reset_is_flagged_in_subject_and_body(monkeypatch):
    log = _capture(monkeypatch)
    rc = rar.main(["--by", "cc-fleet-health", "--base", "cc-storefront", "--session", "storefront",
                   "--handoff", "/h", "--reason", "r", "--forced-gates", "busy,queued",
                   "--busy-reason", "foreground turn in progress ('esc to interrupt')"])
    assert rc == 0
    _, params = [x for x in log if "INSERT" in x[0]][0]
    assert "FORCED" in params[2]
    assert "force=true" in params[3] and "busy,queued" in params[3]
    assert "esc to interrupt" in params[3]


def test_unforced_reset_says_force_false(monkeypatch):
    log = _capture(monkeypatch)
    assert rar.main(["--by", "cc-fleet-health", "--base", "cc-quality", "--session", "quality",
                     "--handoff", "/h", "--reason", "r"]) == 0
    _, params = [x for x in log if "INSERT" in x[0]][0]
    assert "FORCED" not in params[2] and "force=false" in params[3]
