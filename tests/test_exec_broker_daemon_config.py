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
_real_start_heartbeat = daemon._start_heartbeat


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


_HEARTBEAT_CALLS: list = []


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
        "EXEC_BROKER_LIVE_GATED_REFS",
        "EXEC_BROKER_HEARTBEAT_INTERVAL_SECONDS",
        "EXEC_BROKER_GATE_MAX_AGE_SECONDS",
        "EXEC_BROKER_GATE_NOT_BEFORE",
    ):
        monkeypatch.delenv(v, raising=False)
    # main() tests exercise config wiring; the heartbeat take (a real DB call)
    # is replaced here and tested on its own below.
    _HEARTBEAT_CALLS.clear()
    monkeypatch.setattr(daemon, "_start_heartbeat", lambda **kw: _HEARTBEAT_CALLS.append(kw))


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


# --- B1 (bus #89814 §1): standing EXEC_BROKER_LIVE_GATED_REFS ---------------

def test_load_live_gated_refs_defaults_to_qa_only_when_unset_or_blank():
    for raw in (None, "", "   "):
        assert daemon.load_live_gated_refs(raw) == frozenset({"oovskwogvefnznfowjky"})


def test_load_live_gated_refs_resolves_aliases_case_insensitively():
    assert daemon.load_live_gated_refs(" QA , goumlyne") == frozenset(
        {"oovskwogvefnznfowjky", "goumlynecruxrlmzlntp"}
    )
    assert daemon.load_live_gated_refs("goumlyne") == frozenset({"goumlynecruxrlmzlntp"})


@pytest.mark.parametrize("raw", ["not-a-silo", "qa,typo", "qa,,goumlyne", "goumlynecruxrlmzlntp", "qa,"])
def test_load_live_gated_refs_fails_loud_on_unknown_alias_empty_entry_or_raw_ref(raw):
    with pytest.raises(ValueError):
        daemon.load_live_gated_refs(raw)


def test_main_fails_closed_on_bad_live_gated_refs(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_LIVE_GATED_REFS", "qa,not-a-silo")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 1


def test_main_wires_default_live_gated_refs_when_unset(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    assert _FakeServer.last_kwargs["live_gated_refs"] == frozenset({"oovskwogvefnznfowjky"})


def test_main_wires_widened_live_gated_refs(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_LIVE_GATED_REFS", "qa,goumlyne")
    monkeypatch.setenv("EXEC_BROKER_GATE_NOT_BEFORE", "2026-11-05T00:00:00Z")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    assert _FakeServer.last_kwargs["live_gated_refs"] == frozenset(
        {"oovskwogvefnznfowjky", "goumlynecruxrlmzlntp"}
    )


# --- B3 (bus #89814 §4.1): EXEC_BROKER_GATE_MAX_AGE_SECONDS ------------------

def test_load_gate_max_age_defaults_to_24h():
    for raw in (None, "", "  "):
        assert daemon.load_gate_max_age_seconds(raw) == 86400


def test_load_gate_max_age_parses_positive_seconds():
    assert daemon.load_gate_max_age_seconds(" 3600 ") == 3600


@pytest.mark.parametrize("raw", ["0", "-5", "1.5", "24h", "abc", "٣"])
def test_load_gate_max_age_fails_loud_on_bad_values(raw):
    with pytest.raises(ValueError):
        daemon.load_gate_max_age_seconds(raw)


def test_main_fails_closed_on_bad_gate_max_age(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_GATE_MAX_AGE_SECONDS", "0")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 1


def test_main_wires_gate_max_age(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    assert _FakeServer.last_kwargs["gate_max_age_seconds"] == 86400
    monkeypatch.setenv("EXEC_BROKER_GATE_MAX_AGE_SECONDS", "7200")
    assert daemon.main([]) == 0
    assert _FakeServer.last_kwargs["gate_max_age_seconds"] == 7200


# --- B3 rulings c + e (console #89841) --------------------------------------

def test_load_gate_max_age_accepts_the_72h_ceiling_and_refuses_above_it():
    assert daemon.load_gate_max_age_seconds(str(72 * 3600)) == 72 * 3600
    assert daemon.load_gate_max_age_seconds("1") == 1
    for raw in (str(72 * 3600 + 1), "999999999"):
        with pytest.raises(ValueError):
            daemon.load_gate_max_age_seconds(raw)


@pytest.mark.parametrize("raw", [str(72 * 3600 + 1), "0", "-1"])
def test_main_refuses_boot_on_out_of_bounds_gate_max_age(monkeypatch, tmp_path, raw):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_GATE_MAX_AGE_SECONDS", raw)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 1


def test_load_gate_not_before_parses_iso_and_treats_naive_as_utc():
    assert daemon.load_gate_not_before(None) is None
    assert daemon.load_gate_not_before("  ") is None
    assert daemon.load_gate_not_before("2026-11-05T00:00:00Z") == datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert daemon.load_gate_not_before("2026-11-05T08:00:00") == datetime(2026, 11, 5, 8, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        daemon.load_gate_not_before("tomorrow")


def test_main_refuses_boot_on_malformed_gate_not_before(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_GATE_NOT_BEFORE", "not-a-date")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 1


def test_main_refuses_boot_when_non_qa_refs_are_gated_without_a_cutover_floor(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_LIVE_GATED_REFS", "qa,goumlyne")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 1


def test_main_boots_qa_only_without_a_floor_and_wires_none(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    assert _FakeServer.last_kwargs["gate_not_before"] is None


def test_main_wires_the_gate_not_before_floor(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_GATE_NOT_BEFORE", "2026-11-05T00:00:00Z")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    assert _FakeServer.last_kwargs["gate_not_before"] == datetime(2026, 11, 5, tzinfo=timezone.utc)
def test_main_logs_the_resolved_live_gated_refs_once_at_boot(monkeypatch, tmp_path, capsys):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    err = capsys.readouterr().err
    assert err.count("LIVE_GATED_REFS") == 1
    assert "default -- env unset" in err and "irsyad-qa/qa=oovskwogvefnznfowjky" in err
    monkeypatch.setenv("EXEC_BROKER_LIVE_GATED_REFS", "qa,goumlyne")
    monkeypatch.setenv("EXEC_BROKER_GATE_NOT_BEFORE", "2026-11-05T00:00:00Z")  # B3: non-QA needs a floor
    assert daemon.main([]) == 0
    err = capsys.readouterr().err
    assert "goumlyne=goumlynecruxrlmzlntp" in err and "irsyad-qa/qa=oovskwogvefnznfowjky" in err


# --- dead-man heartbeat (spec bus #89814 §5.2) -------------------------------

def test_load_heartbeat_interval_default_and_bounds():
    assert daemon.load_heartbeat_interval(None) == 60
    assert daemon.load_heartbeat_interval(" 120 ") == 120
    for bad in ("0", "9", "901", "1m", "-60"):
        with pytest.raises(ValueError):
            daemon.load_heartbeat_interval(bad)


def test_main_refuses_boot_on_bad_heartbeat_interval(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_HEARTBEAT_INTERVAL_SECONDS", "5")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 1


def test_main_starts_the_heartbeat_for_every_gated_ref_before_serving(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EXEC_BROKER_LIVE_GATED_REFS", "qa,goumlyne")
    # B3 (ruling e): a non-QA gated ref needs a cutover floor, else boot is refused
    monkeypatch.setenv("EXEC_BROKER_GATE_NOT_BEFORE", "2026-11-05T00:00:00Z")
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)
    assert daemon.main([]) == 0
    (call,) = _HEARTBEAT_CALLS
    assert call["refs"] == frozenset({"oovskwogvefnznfowjky", "goumlynecruxrlmzlntp"})
    assert call["interval_seconds"] == 60


def test_main_refuses_boot_when_the_heartbeat_cannot_start(monkeypatch, tmp_path):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)

    def _fail(**kw):
        raise RuntimeError("held by another live broker")

    monkeypatch.setattr(daemon, "_start_heartbeat", _fail)
    assert daemon.main([]) == 1


class _HbConn:
    def __init__(self, take_ok=True, boom=False):
        self.take_ok, self.boom = take_ok, boom

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        conn = self

        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                if conn.boom:
                    raise RuntimeError("db down")
                self._r = {"boot_id": "x"} if conn.take_ok else None

            def fetchone(self):
                return self._r

        return _C()

    def commit(self):
        pass

    def rollback(self):
        pass


def test_start_heartbeat_refuses_on_live_foreign_holder_db_error_or_no_host(monkeypatch):
    import fleet_host_id
    monkeypatch.setattr(fleet_host_id, "fleet_host_id", lambda: "gzb")
    with pytest.raises(RuntimeError, match="another LIVE broker"):
        _real_start_heartbeat(db_conn_factory=lambda: _HbConn(take_ok=False),
                              refs=frozenset({"oovskwogvefnznfowjky"}), interval_seconds=60)
    with pytest.raises(RuntimeError, match="take failed"):
        _real_start_heartbeat(db_conn_factory=lambda: _HbConn(boom=True),
                              refs=frozenset({"oovskwogvefnznfowjky"}), interval_seconds=60)

    def _nohost():
        raise OSError("gethostname failed")

    monkeypatch.setattr(fleet_host_id, "fleet_host_id", _nohost)
    with pytest.raises(RuntimeError, match="host identity"):
        _real_start_heartbeat(db_conn_factory=lambda: _HbConn(),
                              refs=frozenset({"oovskwogvefnznfowjky"}), interval_seconds=60)


def test_start_heartbeat_takes_then_starts_the_writer(monkeypatch):
    import fleet_host_id
    monkeypatch.setattr(fleet_host_id, "fleet_host_id", lambda: "gzb")
    writer = _real_start_heartbeat(db_conn_factory=lambda: _HbConn(),
                                   refs=frozenset({"oovskwogvefnznfowjky"}), interval_seconds=60)
    try:
        assert writer.host == "gzb" and writer.ttl_seconds == 180
        assert writer._thread is not None and writer._thread.is_alive()
    finally:
        writer.stop()


def test_main_refuses_boot_cleanly_on_a_heartbeat_value_error(monkeypatch, tmp_path, capsys):
    _set_base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "BrokerServer", _FakeServer)

    def _bad(**kw):
        raise ValueError("heartbeat needs at least one gated ref")

    monkeypatch.setattr(daemon, "_start_heartbeat", _bad)
    assert daemon.main([]) == 1
    assert "heartbeat needs at least one gated ref" in capsys.readouterr().err


def test_real_start_heartbeat_with_no_refs_raises_value_error(monkeypatch):
    import fleet_host_id
    monkeypatch.setattr(fleet_host_id, "fleet_host_id", lambda: "gzb")
    with pytest.raises(ValueError):
        _real_start_heartbeat(db_conn_factory=lambda: None, refs=frozenset(), interval_seconds=60)
