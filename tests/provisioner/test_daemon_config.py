"""provisioner daemon — launchd ExecStart target config surface (mirrors
exec_broker_daemon.py's tested-in-isolation config helpers).

The daemon stays as thin as possible: parse env, resolve the two DSNs the
right way (bus: fresh from the .env file per connection, the op#24342
rotation lesson; store: fresh from a 0600 file minted by orch-console — the
lane itself is residency-barred from the ceayj admin DSN, so the daemon
reads it, tests never see it), wire ProvisionerServer, serve forever.
"""
import os
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts"))

import provisioner_daemon as daemon  # noqa: E402


def test_load_allowed_uids_fail_loud():
    assert daemon.load_allowed_uids("501,  502") == frozenset({501, 502})
    for bad in ("", "   ", "501,x", "root", "-1"):
        with pytest.raises(ValueError):
            daemon.load_allowed_uids(bad)


def test_read_store_dsn_is_fresh_per_read(tmp_path):
    # op#24342 lesson, applied to the store DSN too: no startup caching —
    # a rotation's single push-point (the file) is always honored
    f = tmp_path / "store.dsn"
    f.write_text("postgresql://a@h/db\n")
    os.chmod(f, 0o600)
    assert daemon.read_store_dsn(f).endswith("a@h/db")
    f.write_text("postgresql://b@h/db\n")  # rotated
    os.chmod(f, 0o600)
    assert daemon.read_store_dsn(f).endswith("b@h/db")


def test_read_store_dsn_refuses_loose_file_modes(tmp_path):
    # the store DSN is the ceayj admin write credential: a group/world
    # readable file refuses, never quietly proceeds
    f = tmp_path / "store.dsn"
    f.write_text("postgresql://a@h/db\n")
    os.chmod(f, 0o644)
    with pytest.raises(daemon.DaemonConfigError, match="mode"):
        daemon.read_store_dsn(f)
    os.chmod(f, 0o600)
    assert daemon.read_store_dsn(f).endswith("a@h/db")
    empty = tmp_path / "empty.dsn"
    empty.write_text("   \n")
    os.chmod(empty, 0o600)
    with pytest.raises(daemon.DaemonConfigError):
        daemon.read_store_dsn(empty)


def test_bus_conn_factory_sets_dict_row(tmp_path, monkeypatch):
    # the gate re-read expects dict_row rows (from_agent/message_type/...);
    # a daemon wiring a plain cursor would KeyError at the worst moment.
    # Proven by construction: the factory the daemon builds passes
    # row_factory=dict_row to psycopg.connect — tested with a stub connect.
    import provisioner_daemon as d
    calls = {}

    class FakeConn:
        def __init__(self, *a, **kw):
            calls["kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import psycopg.rows
    monkeypatch.setattr(d.psycopg, "connect", lambda *a, **kw: FakeConn(*a, **kw))
    factory = d.build_bus_conn_factory("postgresql://bus")
    with factory() as conn:
        assert conn is not None
    assert calls["kwargs"].get("row_factory") is psycopg.rows.dict_row
