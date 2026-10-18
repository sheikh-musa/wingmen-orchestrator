"""hc#95 exec-broker daemon CLI config parsing (no network/DB/socket I/O)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "exec_broker_daemon.py"


def _load():
    sys.path.insert(0, str(_PATH.parent / "lib"))
    spec = importlib.util.spec_from_file_location("exec_broker_daemon", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load()


def test_load_allowed_uids_parses_csv():
    assert daemon.load_allowed_uids("1000,1001") == frozenset({1000, 1001})


def test_load_allowed_uids_parses_single():
    assert daemon.load_allowed_uids("1000") == frozenset({1000})


def test_load_allowed_uids_strips_whitespace():
    assert daemon.load_allowed_uids(" 1000 , 1001 ") == frozenset({1000, 1001})


def test_load_allowed_uids_rejects_empty():
    with pytest.raises(ValueError):
        daemon.load_allowed_uids("")


def test_load_allowed_uids_rejects_whitespace_only():
    with pytest.raises(ValueError):
        daemon.load_allowed_uids("   ")


def test_load_allowed_uids_rejects_non_digit():
    with pytest.raises(ValueError):
        daemon.load_allowed_uids("1000,abc")


def test_load_allowed_uids_rejects_negative():
    with pytest.raises(ValueError):
        daemon.load_allowed_uids("-1")


def test_main_fails_closed_on_missing_env_vars(monkeypatch):
    for v in daemon._REQUIRED_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    assert daemon.main([]) == 1


def test_main_fails_closed_on_bad_allowed_uids(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("EXEC_BROKER_SOCKET", str(tmp_path / "s.sock"))
    monkeypatch.setenv("EXEC_BROKER_ALLOWED_UIDS", "not-a-uid")
    monkeypatch.setenv("EXEC_BROKER_TOKEN_FILE", str(token_file))
    assert daemon.main([]) == 1


def test_main_fails_closed_on_missing_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("EXEC_BROKER_SOCKET", str(tmp_path / "s.sock"))
    monkeypatch.setenv("EXEC_BROKER_ALLOWED_UIDS", "1000")
    monkeypatch.setenv("EXEC_BROKER_TOKEN_FILE", str(tmp_path / "does-not-exist"))
    assert daemon.main([]) == 1


def test_main_fails_closed_on_empty_token_file(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("EXEC_BROKER_SOCKET", str(tmp_path / "s.sock"))
    monkeypatch.setenv("EXEC_BROKER_ALLOWED_UIDS", "1000")
    monkeypatch.setenv("EXEC_BROKER_TOKEN_FILE", str(token_file))
    assert daemon.main([]) == 1
