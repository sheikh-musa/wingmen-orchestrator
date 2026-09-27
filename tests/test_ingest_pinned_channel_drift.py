"""Runtime pinned-channel-drift check (op#22669 item 3, orch-console bus #43933).

The static guard (tests/migrations/test_ingest_channels_enabled_false.py) only
proves the "a pinned channel ships enabled=false" invariant at
migration-authoring time by parsing migrations/*.sql + boot script env. It
cannot catch a LIVE row drifting to enabled=true after deploy (manual UPDATE,
a future migration flipping it back, replication skew) — the exact
precondition for the dual-poller 409 class (bus #43775/#43833, channel
'oeh'). This exercises the runtime backstop in nervous_system/ingest.py
against the ephemeral PG17 harness (tests/conftest.py's pg_dsn). NEVER
touches DATABASE_URL / any live silo.
"""
from __future__ import annotations

import psycopg
import pytest

from nervous_system import ingest


@pytest.fixture
def drift_dsn(pg_dsn, monkeypatch):
    # Paging goes through scripts/bus_send.send(), which opens its OWN
    # connection via dsn=_dsn() -- pin INGEST_DSN to the ephemeral instance so
    # it can never fall through to this shell's real DATABASE_URL (the live
    # substrate) if that env var happens to be set.
    monkeypatch.setenv("INGEST_DSN", pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute(
            """CREATE TABLE bot_channels (
                 channel_key text PRIMARY KEY,
                 enabled boolean NOT NULL DEFAULT false
               )"""
        )
        cur.execute(
            """CREATE TABLE agent_messages (
                 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                 from_agent text NOT NULL,
                 to_agent text NOT NULL,
                 message_type text NOT NULL,
                 subject text,
                 body text,
                 priority text,
                 requires_response boolean NOT NULL DEFAULT false,
                 thread_id uuid,
                 created_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
    return pg_dsn


def _seed(dsn: str, **rows: bool) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for key, enabled in rows.items():
            cur.execute(
                "INSERT INTO bot_channels (channel_key, enabled) VALUES (%s, %s) "
                "ON CONFLICT (channel_key) DO UPDATE SET enabled = EXCLUDED.enabled",
                (key, enabled))


# --------------------------------------------------------------------------------
# pinned_keys(): reads INGEST_CHANNELS
# --------------------------------------------------------------------------------

def test_pinned_keys_empty_when_unset(monkeypatch):
    monkeypatch.delenv("INGEST_CHANNELS", raising=False)
    assert ingest.pinned_keys() == []


def test_pinned_keys_parses_csv(monkeypatch):
    monkeypatch.setenv("INGEST_CHANNELS", " nazim-console , oeh ,")
    assert ingest.pinned_keys() == ["nazim-console", "oeh"]


# --------------------------------------------------------------------------------
# drifted_pinned_channels(): the hub (no pin) never flags anything
# --------------------------------------------------------------------------------

def test_hub_with_no_pins_never_drifts(drift_dsn):
    _seed(drift_dsn, oeh=True)
    with psycopg.connect(drift_dsn) as conn:
        assert ingest.drifted_pinned_channels(conn, []) == []


def test_pinned_channel_enabled_false_is_clean(drift_dsn):
    _seed(drift_dsn, **{"nazim-console": False})
    with psycopg.connect(drift_dsn) as conn:
        assert ingest.drifted_pinned_channels(conn, ["nazim-console"]) == []


def test_pinned_channel_enabled_true_is_drift(drift_dsn):
    _seed(drift_dsn, **{"nazim-console": True})
    with psycopg.connect(drift_dsn) as conn:
        assert ingest.drifted_pinned_channels(conn, ["nazim-console"]) == ["nazim-console"]


def test_only_pinned_keys_are_checked_not_every_enabled_channel(drift_dsn):
    """A hub-owned enabled=true channel that ISN'T in this host's pin list must
    never be reported — only the pinned host's own drift matters here."""
    _seed(drift_dsn, **{"nazim-console": False, "operator-orch": True})
    with psycopg.connect(drift_dsn) as conn:
        assert ingest.drifted_pinned_channels(conn, ["nazim-console"]) == []


# --------------------------------------------------------------------------------
# check_pinned_channels_not_enabled(): loud log + page-once end to end
# --------------------------------------------------------------------------------

def test_check_pages_once_on_drift(drift_dsn, monkeypatch, capsys):
    monkeypatch.setenv("INGEST_CHANNELS", "nazim-console")
    _seed(drift_dsn, **{"nazim-console": True})
    with psycopg.connect(drift_dsn) as conn:
        drifted = ingest.check_pinned_channels_not_enabled(conn, host="mini-1")
        assert drifted == ["nazim-console"]
    out = capsys.readouterr().out
    assert "WATCHDOG" in out and "nazim-console" in out and "enabled=true LIVE" in out

    with psycopg.connect(drift_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT from_agent, to_agent, priority, requires_response, body FROM agent_messages")
        rows = cur.fetchall()
    assert len(rows) == 1
    from_agent, to_agent, priority, rr, body = rows[0]
    assert from_agent == "ingest-watchdog"
    assert to_agent == "orch-console"
    assert priority == "P1"
    assert rr is True
    assert "PINNED-CHANNEL-DRIFT:nazim-console:mini-1" in body


def test_check_does_not_repage_the_same_channel_host(drift_dsn, monkeypatch):
    monkeypatch.setenv("INGEST_CHANNELS", "nazim-console")
    _seed(drift_dsn, **{"nazim-console": True})
    with psycopg.connect(drift_dsn) as conn:
        ingest.check_pinned_channels_not_enabled(conn, host="mini-1")
        ingest.check_pinned_channels_not_enabled(conn, host="mini-1")
        ingest.check_pinned_channels_not_enabled(conn, host="mini-1")
    with psycopg.connect(drift_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        assert cur.fetchone()[0] == 1


def test_check_repages_a_different_host_independently(drift_dsn, monkeypatch):
    """Same drifted channel_key but a DIFFERENT host is a distinct episode —
    e.g. two Mini-class boxes both mis-pinning the same channel_key."""
    monkeypatch.setenv("INGEST_CHANNELS", "nazim-console")
    _seed(drift_dsn, **{"nazim-console": True})
    with psycopg.connect(drift_dsn) as conn:
        ingest.check_pinned_channels_not_enabled(conn, host="mini-1")
        ingest.check_pinned_channels_not_enabled(conn, host="mini-2")
    with psycopg.connect(drift_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        assert cur.fetchone()[0] == 2


def test_check_clean_state_pages_nothing(drift_dsn, monkeypatch, capsys):
    monkeypatch.setenv("INGEST_CHANNELS", "nazim-console")
    _seed(drift_dsn, **{"nazim-console": False})
    with psycopg.connect(drift_dsn) as conn:
        assert ingest.check_pinned_channels_not_enabled(conn, host="mini-1") == []
    assert "WATCHDOG" not in capsys.readouterr().out
    with psycopg.connect(drift_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        assert cur.fetchone()[0] == 0


def test_check_no_pin_at_all_pages_nothing(drift_dsn, monkeypatch, capsys):
    monkeypatch.delenv("INGEST_CHANNELS", raising=False)
    _seed(drift_dsn, oeh=True)  # hub-owned, enabled=true is NORMAL for the hub
    with psycopg.connect(drift_dsn) as conn:
        assert ingest.check_pinned_channels_not_enabled(conn, host="hub-1") == []
    assert "WATCHDOG" not in capsys.readouterr().out
    with psycopg.connect(drift_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_messages")
        assert cur.fetchone()[0] == 0


# --------------------------------------------------------------------------------
# _run_pinned_channel_drift_check(): the check must NEVER cost main()'s loop an
# ERROR_BACKOFF cycle or a `continue` -- isolated from load_channels' own
# try/except (orch-console bus #43985, PR #182 change 2).
# --------------------------------------------------------------------------------

def test_drift_check_wrapper_swallows_any_exception(drift_dsn, monkeypatch, capsys):
    def _boom(conn, host=None):
        raise RuntimeError("simulated bus_send hiccup")

    monkeypatch.setattr(ingest, "check_pinned_channels_not_enabled", _boom)
    ingest._run_pinned_channel_drift_check()   # must not raise
    out = capsys.readouterr().out
    assert "WATCHDOG" in out and "pinned-channel-drift check failed" in out and "ingest continues" in out


def test_drift_check_wrapper_clean_run_is_silent(drift_dsn, monkeypatch, capsys):
    # drift_dsn pins INGEST_DSN to the ephemeral instance -- _dsn() must never
    # fall through to a real DATABASE_URL here.
    monkeypatch.delenv("INGEST_CHANNELS", raising=False)
    ingest._run_pinned_channel_drift_check()
    assert "WATCHDOG" not in capsys.readouterr().out
