"""hc#95 exec-broker daemon CLI config parsing (no network/DB/socket I/O)."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
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


# --- bounded override (op#34322/#76985/#76989/#76998) -----------------------

_NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def test_resolve_ref_override_alias_resolves_known_alias():
    assert daemon.resolve_ref_override_alias("goumlyne") == "goumlynecruxrlmzlntp"


def test_resolve_ref_override_alias_is_case_insensitive():
    assert daemon.resolve_ref_override_alias("GOUMLYNE") == "goumlynecruxrlmzlntp"


def test_resolve_ref_override_alias_inert_when_none():
    assert daemon.resolve_ref_override_alias(None) is None


def test_resolve_ref_override_alias_inert_when_blank():
    assert daemon.resolve_ref_override_alias("   ") is None


def test_resolve_ref_override_alias_rejects_unknown_alias():
    with pytest.raises(ValueError):
        daemon.resolve_ref_override_alias("not-a-real-alias")


def test_resolve_ref_override_alias_rejects_a_raw_ref_passed_by_mistake():
    # The whole point is alias-only -- a raw ref typed into this var must be
    # refused exactly like any other unknown alias, never silently accepted.
    with pytest.raises(ValueError):
        daemon.resolve_ref_override_alias("goumlynecruxrlmzlntp")


def test_parse_override_expiry_parses_iso8601():
    got = daemon.parse_override_expiry("2026-10-26T18:00:00+00:00", now=_NOW)
    assert got == datetime(2026, 10, 26, 18, 0, 0, tzinfo=timezone.utc)


def test_parse_override_expiry_accepts_z_suffix():
    got = daemon.parse_override_expiry("2026-10-26T18:00:00Z", now=_NOW)
    assert got == datetime(2026, 10, 26, 18, 0, 0, tzinfo=timezone.utc)


def test_parse_override_expiry_treats_naive_as_utc():
    got = daemon.parse_override_expiry("2026-10-26T18:00:00", now=_NOW)
    assert got == datetime(2026, 10, 26, 18, 0, 0, tzinfo=timezone.utc)


def test_parse_override_expiry_inert_when_none():
    assert daemon.parse_override_expiry(None, now=_NOW) is None


def test_parse_override_expiry_inert_when_blank():
    assert daemon.parse_override_expiry("  ", now=_NOW) is None


def test_parse_override_expiry_rejects_malformed():
    with pytest.raises(ValueError):
        daemon.parse_override_expiry("not-a-date", now=_NOW)


def test_parse_override_expiry_clamps_to_24h_cap():
    got = daemon.parse_override_expiry("2026-10-30T12:00:00+00:00", now=_NOW)
    assert got == _NOW + timedelta(hours=24)


def test_parse_override_expiry_within_cap_is_not_clamped():
    got = daemon.parse_override_expiry("2026-10-26T13:00:00+00:00", now=_NOW)
    assert got == _NOW + timedelta(hours=1)


def _set_base_env(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("EXEC_BROKER_SOCKET", str(tmp_path / "s.sock"))
    monkeypatch.setenv("EXEC_BROKER_ALLOWED_UIDS", "1000")
    monkeypatch.setenv("EXEC_BROKER_TOKEN_FILE", str(token_file))
    for v in (
        "EXEC_BROKER_REF_OVERRIDE_ALIAS",
        "EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT",
        "EXEC_BROKER_UID_OVERRIDE",
        "EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT",
    ):
        monkeypatch.delenv(v, raising=False)


class _FakeServer:
    """Captures BrokerServer(**kwargs) and never actually serves."""

    last_kwargs = None

    def __init__(self, **kwargs):
        _FakeServer.last_kwargs = kwargs

    def serve_forever(self):
        return None


def test_main_fails_closed_on_unknown_ref_alias(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_REF_OVERRIDE_ALIAS", "not-a-real-alias")
    monkeypatch.setenv("EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT", "2026-10-26T18:00:00Z")
    assert daemon.main([]) == 1


def test_main_fails_closed_ref_alias_without_expiry(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_REF_OVERRIDE_ALIAS", "goumlyne")
    assert daemon.main([]) == 1


def test_main_fails_closed_ref_expiry_without_alias(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT", "2026-10-26T18:00:00Z")
    assert daemon.main([]) == 1


def test_main_fails_closed_uid_override_without_expiry(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_UID_OVERRIDE", "2000")
    assert daemon.main([]) == 1


def test_main_fails_closed_uid_expiry_without_override(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT", "2026-10-26T18:00:00Z")
    assert daemon.main([]) == 1


def test_main_wires_resolved_ref_and_uid_overrides_into_broker_server(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_REF_OVERRIDE_ALIAS", "goumlyne")
    monkeypatch.setenv("EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT", "2026-10-26T18:00:00Z")
    monkeypatch.setenv("EXEC_BROKER_UID_OVERRIDE", "2000")
    monkeypatch.setenv("EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT", "2026-10-26T18:00:00Z")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    kwargs = _FakeServer.last_kwargs
    # The server receives the RESOLVED raw ref, never the alias string -- the
    # alias is only ever a config-time convenience for the root-holder typing
    # the env var; check_project_ref still compares against real refs.
    assert kwargs["ref_override"] == "goumlynecruxrlmzlntp"
    assert kwargs["uid_override"] == frozenset({2000})
    assert kwargs["ref_override_expires_at"] is not None
    assert kwargs["uid_override_expires_at"] is not None


def test_main_overrides_inert_when_all_unset(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    kwargs = _FakeServer.last_kwargs
    assert kwargs["ref_override"] is None
    assert kwargs["ref_override_expires_at"] is None
    assert kwargs["uid_override"] is None
    assert kwargs["uid_override_expires_at"] is None
