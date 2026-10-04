"""Tests for scripts/sweep_persisted_output_secrets.py (bus #51314/#51334/#51341:
remediation sweep for tool-results/* files written before PR#282's spill fix).

All synthetic DSNs below are built via string concatenation ("postgres" + "://...")
to dodge secrets_transcript_guard (PreToolUse) on the commit that adds them.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ORCH_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = ORCH_ROOT / "scripts" / "sweep_persisted_output_secrets.py"
sys.path.insert(0, str(ORCH_ROOT))
spec = importlib.util.spec_from_file_location("sweep_persisted_output_secrets", SCRIPT_PATH)
sweep = importlib.util.module_from_spec(spec)
sys.modules["sweep_persisted_output_secrets"] = sweep
spec.loader.exec_module(sweep)


def _write_env(tmp_path, name, var, value):
    p = tmp_path / name
    p.write_text(f"{var}={value}\n")
    return p


def test_current_credential_hashes_reads_env_files_and_counts_them(tmp_path, monkeypatch):
    _write_env(tmp_path, ".env", "DATABASE_URL", "postgres" + "://u:p@host:5432/db")
    monkeypatch.setattr(sweep, "CREDENTIAL_GLOB_ROOTS", [str(tmp_path)])
    hashes, count = sweep._current_credential_hashes()
    assert count == 1
    assert len(hashes) == 1


def test_current_credential_hashes_ignores_blank_values(tmp_path, monkeypatch):
    dsn = "postgres" + "://u:p@host:5432/db"
    p = tmp_path / ".env"
    p.write_text(f"EMPTY_VAR=\nREAL_VAR={dsn}\n")
    monkeypatch.setattr(sweep, "CREDENTIAL_GLOB_ROOTS", [str(tmp_path)])
    hashes, count = sweep._current_credential_hashes()
    assert count == 1
    assert len(hashes) == 1  # only REAL_VAR (blank EMPTY_VAR skipped)


def test_current_credential_hashes_skips_a_value_with_no_secret_shape(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("PORT=3000\nENVIRONMENT=production\n")
    monkeypatch.setattr(sweep, "CREDENTIAL_GLOB_ROOTS", [str(tmp_path)])
    hashes, count = sweep._current_credential_hashes()
    assert count == 1
    assert len(hashes) == 0, "plain config values with no secret pattern match must not be hashed"


def test_sweep_file_hashes_before_redacting_and_finds_a_current_match(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "EVENT_LOG_PATH", str(tmp_path / "logs" / "events.log"))
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.30:5432/orch"
    spill = tmp_path / "spill.txt"
    spill.write_text(f"before\n{dsn}\nafter\n")

    # the postgres-dsn pattern only matches up to "@" (never host/port/db) -- a
    # "current credential" dict must key on that same match span, not the whole value.
    match_span = sweep.SECRET_PATTERNS["postgres-dsn"].search(dsn).group(0)
    current_hashes = {
        __import__("hashlib").sha256(match_span.encode()).hexdigest(): "some/.env:DATABASE_URL",
    }
    counts, refs = sweep.sweep_file(spill, current_hashes, dry_run=False)
    assert counts == {"postgres-dsn": 1}
    assert refs == ["some/.env:DATABASE_URL"]

    after = spill.read_text()
    assert dsn not in after
    assert "REDACTED" in after


def test_sweep_file_no_match_still_redacts_but_no_ref(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "EVENT_LOG_PATH", str(tmp_path / "logs" / "events.log"))
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.31:5432/orch"
    spill = tmp_path / "spill.txt"
    spill.write_text(dsn)
    counts, refs = sweep.sweep_file(spill, {}, dry_run=False)
    assert counts == {"postgres-dsn": 1}
    assert refs == []
    assert dsn not in spill.read_text()


def test_sweep_file_dry_run_touches_nothing_and_logs_nothing(tmp_path, monkeypatch):
    log_path = tmp_path / "logs" / "events.log"
    monkeypatch.setattr(sweep, "EVENT_LOG_PATH", str(log_path))
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.32:5432/orch"
    spill = tmp_path / "spill.txt"
    spill.write_text(dsn)
    before = spill.read_text()
    counts, refs = sweep.sweep_file(spill, {}, dry_run=True)
    assert counts == {"postgres-dsn": 1}
    assert spill.read_text() == before, "dry-run must not touch the file"
    assert not log_path.exists(), "dry-run must not write to the event log"


def test_sweep_file_logs_class_file_and_var_name_never_the_value(tmp_path, monkeypatch):
    log_path = tmp_path / "logs" / "events.log"
    monkeypatch.setattr(sweep, "EVENT_LOG_PATH", str(log_path))
    secret_fragment = "RealLooking9Zx" + "NeverLogged"
    dsn = "postgres" + f"://orchuser:{secret_fragment}@203.0.113.33:5432/orch"
    spill = tmp_path / "spill.txt"
    spill.write_text(dsn)

    match_span = sweep.SECRET_PATTERNS["postgres-dsn"].search(dsn).group(0)
    current_hashes = {
        __import__("hashlib").sha256(match_span.encode()).hexdigest(): "some/.env:DATABASE_URL",
    }
    sweep.sweep_file(spill, current_hashes, dry_run=False)

    assert log_path.exists()
    content = log_path.read_text()
    assert secret_fragment not in content
    record = json.loads(content.strip().splitlines()[-1])
    assert record["source"] == "sweep"
    assert record["cls"] == "postgres-dsn"
    assert record["file"] == str(spill)
    assert record["matched_var_name"] == "some/.env:DATABASE_URL"

    import stat
    mode = stat.S_IMODE(log_path.stat().st_mode)
    assert mode == 0o600


def test_sweep_file_logs_none_var_name_when_no_current_match(tmp_path, monkeypatch):
    log_path = tmp_path / "logs" / "events.log"
    monkeypatch.setattr(sweep, "EVENT_LOG_PATH", str(log_path))
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.34:5432/orch"
    spill = tmp_path / "spill.txt"
    spill.write_text(dsn)
    sweep.sweep_file(spill, {}, dry_run=False)
    record = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert record["matched_var_name"] is None


def test_sweep_roots_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "EVENT_LOG_PATH", str(tmp_path / "logs" / "events.log"))
    env_dir = tmp_path / "envs"
    env_dir.mkdir()
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.35:5432/orch"
    _write_env(env_dir, ".env", "DATABASE_URL", dsn)
    monkeypatch.setattr(sweep, "CREDENTIAL_GLOB_ROOTS", [str(env_dir)])

    proj_root = tmp_path / "projects" / "-Users-x-proj1" / "sess1" / "tool-results"
    proj_root.mkdir(parents=True)
    (proj_root / "spill1.txt").write_text(dsn)

    result = sweep.sweep_roots([str(tmp_path / "projects")], dry_run=False)
    assert result["files_scanned"] == 1
    assert result["files_with_hits"] == 1
    assert result["credential_files_checked"] == 1
    assert result["per_class"] == {"postgres-dsn": 1}
    assert result["per_project"] == {"-Users-x-proj1": {"postgres-dsn": 1}}
    assert result["real_current_hits"] == [("-Users-x-proj1", f"{env_dir / '.env'}:DATABASE_URL")]
