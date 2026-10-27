"""reset_audit_row — the pre-clear audit row reset_auditor.sh writes BEFORE any keystroke.

Exit code is the contract: 0 only after the row COMMITS; non-zero on any failure, so the
caller aborts with the body untouched (dead-man's switch). DSN is read FILE-FIRST
(substrate_dsn.dsn_from_env_file) — a long-lived caller's inherited DATABASE_URL may hold a
pre-rotation password, and a stale-password connect extends the pooler circuit-breaker
(op#24342).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "lib"))
import reset_audit_row as rar  # noqa: E402


def _capture(monkeypatch, dsn="postgres://f"):
    calls = []
    monkeypatch.setattr(rar, "dsn_from_env_file", lambda: dsn)
    def fake_send(**kw):
        calls.append(kw)
        return (1, "thread")
    monkeypatch.setattr(rar.bus_send, "send", fake_send)
    return calls


def test_writes_self_addressed_audit_row_and_commits(monkeypatch):
    calls = _capture(monkeypatch, dsn="postgres://file-first")
    rc = rar.main(["--by", "cc-fleet-health", "--base", "cc-storefront", "--session", "storefront",
                   "--handoff", "/abs/h.md", "--reason", "bloat 832k"])
    assert rc == 0
    kw = calls[0]
    assert kw["dsn"] == "postgres://file-first", "must use the FILE-first DSN"
    assert kw["from_agent"] == "cc-fleet-health" and kw["to"] == "cc-fleet-health"  # self-addressed
    assert "cc-storefront" in kw["subject"] and "/abs/h.md" in kw["body"] and "bloat 832k" in kw["body"]


def test_db_failure_is_nonzero(monkeypatch):
    monkeypatch.setattr(rar, "dsn_from_env_file", lambda: "postgres://x")
    def boom(**kw): raise RuntimeError("pooler down")
    monkeypatch.setattr(rar.bus_send, "send", boom)
    assert rar.main(["--by", "cc-fleet-health", "--base", "cc-quality", "--session", "quality",
                     "--handoff", "/h", "--reason", "r"]) != 0


def test_missing_dsn_is_nonzero(monkeypatch):
    def nodsn(): raise RuntimeError("no DATABASE_URL")
    monkeypatch.setattr(rar, "dsn_from_env_file", nodsn)
    assert rar.main(["--by", "cc-fleet-health", "--base", "cc-quality", "--session", "quality",
                     "--handoff", "/h", "--reason", "r"]) != 0


def test_forced_reset_is_flagged_in_subject_and_body(monkeypatch):
    calls = _capture(monkeypatch)
    rc = rar.main(["--by", "cc-fleet-health", "--base", "cc-storefront", "--session", "storefront",
                   "--handoff", "/h", "--reason", "r", "--forced-gates", "busy,queued",
                   "--busy-reason", "foreground turn in progress ('esc to interrupt')"])
    assert rc == 0
    kw = calls[0]
    assert "FORCED" in kw["subject"]
    assert "force=true" in kw["body"] and "busy,queued" in kw["body"]
    assert "esc to interrupt" in kw["body"]


def test_unforced_reset_says_force_false(monkeypatch):
    calls = _capture(monkeypatch)
    assert rar.main(["--by", "cc-fleet-health", "--base", "cc-quality", "--session", "quality",
                     "--handoff", "/h", "--reason", "r"]) == 0
    kw = calls[0]
    assert "FORCED" not in kw["subject"] and "force=false" in kw["body"]


def test_bus_send_import_resolves_under_real_reset_auditor_invocation():
    """Musa op#34845, bus #78369 finding C (verified): reset_auditor.sh:168 invokes this
    file by its PLAIN PATH (`$PY "$_LIB/reset_audit_row.py" ...`), with no PYTHONPATH --
    unlike pytest, which has `pythonpath = .` in pytest.ini and so always sees `scripts` as
    an importable package regardless of what this file's own import logic does. Under the
    real invocation, sys.path[0] is scripts/lib/ (this file's own directory), which contains
    neither a `scripts` package nor bus_send.py (that lives one level up, in scripts/) -- so
    BOTH of this file's import attempts (`from scripts import bus_send` and the plain
    `import bus_send` fallback) raised ModuleNotFoundError at module-import time, before
    argparse even ran, before this test file's own import of reset_audit_row even existed to
    mask it. That crashed every real /clear through reset_auditor.sh (dead-man's switch: no
    audit row written -> reset_auditor.sh aborts). Reproduced directly: `env -i HOME=... PATH=...
    .venv/bin/python3 scripts/lib/reset_audit_row.py --help` raised exactly this traceback
    before the fix. This test invokes the real file as a real subprocess, in a clean env, from
    a cwd that is NOT the repo root (so nothing but the fix itself can make the import
    resolve) -- the same invocation shape reset_auditor.sh uses minus the DSN/args, which
    `--help` lets us skip (argparse exits 0 before needing them, but only once the module
    itself can load)."""
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "lib" / "reset_audit_row.py"
    clean_env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd="/tmp", env=clean_env, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"reset_audit_row.py crashed on import under a real reset_auditor.sh-style "
        f"invocation (clean env, no PYTHONPATH):\n{result.stderr}"
    )
    assert "ModuleNotFoundError" not in result.stderr
