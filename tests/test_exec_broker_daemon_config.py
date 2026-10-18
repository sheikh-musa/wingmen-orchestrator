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


def test_main_fails_closed_when_database_url_unresolvable(monkeypatch, tmp_path):
    # op#24342: DATABASE_URL is no longer read from os.environ directly (that
    # was the stale-DSN bug class) -- it's resolved via
    # substrate_dsn.dsn_from_env_file, which fails loud when neither the file
    # nor the environment has it.
    token_file = tmp_path / "token"
    token_file.write_text("secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("EXEC_BROKER_ENV_FILE", str(tmp_path / "does-not-exist-env"))
    monkeypatch.setenv("EXEC_BROKER_SOCKET", str(tmp_path / "s.sock"))
    monkeypatch.setenv("EXEC_BROKER_ALLOWED_UIDS", "1000")
    monkeypatch.setenv("EXEC_BROKER_TOKEN_FILE", str(token_file))
    assert daemon.main([]) == 1


def test_db_conn_factory_resolves_dsn_via_substrate_dsn_file_first(monkeypatch, tmp_path):
    # Proves exec_broker_daemon.py actually WIRES into substrate_dsn (file
    # wins over the environment), not just os.environ["DATABASE_URL"].
    env_file = tmp_path / "broker.env"
    env_file.write_text("DATABASE_URL=postgresql://from-file\n")
    monkeypatch.setenv("EXEC_BROKER_ENV_FILE", str(env_file))
    monkeypatch.setenv("DATABASE_URL", "postgresql://stale-inherited-env-value")

    captured = {}

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        return _FakeConn()

    import psycopg
    monkeypatch.setattr(psycopg, "connect", _fake_connect)
    daemon._db_conn_factory()
    assert captured["dsn"] == "postgresql://from-file"


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
