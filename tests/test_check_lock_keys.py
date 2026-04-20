"""Tests for scripts/ci/check_lock_keys.sh — SA2 (CAI-RESP-054).

The script enforces docs/lock-namespace.md mechanically:
  R1. No hashtext( calls.
  R2. Every pg_*_advisory_*lock( literal ID must be registered.

Fixtures build a tiny fake repo with a docs/lock-namespace.md and a single
code file, then invoke the script against it via --root.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = _REPO_ROOT / "scripts" / "ci" / "check_lock_keys.sh"


def _write_registry(root: Path, registered: list[int]) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f"| {rid}   | TEST_{rid}    | owner | purpose |" for rid in registered
    )
    (docs / "lock-namespace.md").write_text(
        "# registry\n\n"
        "## Active registrations\n\n"
        "| Key ID | Constant name | Owner | Purpose |\n"
        "|--------|--------------|-------|---------|\n"
        f"{rows}\n\n"
        "## Rules\n\nfoo\n"
    )


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
    )


def test_script_is_executable():
    """CI needs the script to be executable without explicit `bash` prefix."""
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/ci/check_lock_keys.sh must be +x"


def test_passes_when_all_keys_registered(tmp_path):
    _write_registry(tmp_path, [1001])
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.py").write_text(
        "def f(cur):\n    cur.execute('SELECT pg_try_advisory_xact_lock(1001)')\n"
    )
    r = _run(tmp_path)
    assert r.returncode == 0, f"expected exit 0, got {r.returncode}. stderr:\n{r.stderr}"


def test_fails_on_hashtext_call(tmp_path):
    """R1: hashtext( is forbidden because 32-bit hash space defeats the registry."""
    _write_registry(tmp_path, [1001])
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text("k = hashtext('my-lock-name')\n")
    r = _run(tmp_path)
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}. stdout:{r.stdout} stderr:{r.stderr}"
    assert "R1 violation" in r.stderr
    assert "hashtext" in r.stderr


def test_fails_on_unregistered_lock_key(tmp_path):
    """R2: literal int argument to pg_*_advisory_*lock must be registered."""
    _write_registry(tmp_path, [1001])  # 1001 registered, 1234 is not
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text(
        "cur.execute('SELECT pg_advisory_lock(1234)')\n"
    )
    r = _run(tmp_path)
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}. stderr:{r.stderr}"
    assert "R2 violation" in r.stderr
    assert "1234" in r.stderr


def test_comment_prose_does_not_false_positive(tmp_path):
    """The regex must not treat prose like `pg_try_advisory_xact_lock (5s retry)`
    as an advisory-lock call — that's a comment, not a call."""
    _write_registry(tmp_path, [1001])
    src = tmp_path / "src"
    src.mkdir()
    (src / "prose.sh").write_text(
        "# pg_try_advisory_xact_lock (5s retry) + scan of agent_status\n"
    )
    r = _run(tmp_path)
    assert r.returncode == 0, f"prose must not false-trigger. stderr:{r.stderr}"


def test_detects_multiple_variants(tmp_path):
    """Covers pg_advisory_lock, pg_try_advisory_lock, pg_try_advisory_xact_lock,
    pg_advisory_xact_unlock — all must be checked."""
    _write_registry(tmp_path, [1001])
    src = tmp_path / "src"
    src.mkdir()
    (src / "variants.sql").write_text(
        "SELECT pg_advisory_lock(9999);\n"  # unregistered → fail
    )
    r = _run(tmp_path)
    assert r.returncode == 1
    assert "9999" in r.stderr
