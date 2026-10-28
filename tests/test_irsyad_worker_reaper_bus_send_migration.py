"""irsyad_worker_reaper.py's two agent_messages write call sites must go through
scripts/bus_send.py, never a raw INSERT INTO agent_messages (Musa op#34845, bus
#78757/#79125/#79950, PR-B step 2 irsyad_*.py migration).

Different answer per call site (settled in bus #79950), BOTH landing on post_on_conn
(not send()), for two different reasons:
- _reap(): the "REAPED worker X" row MUST ride the SAME transaction as the
  fleet_lanes UPDATE + agent_status DELETE it describes -- post_on_conn() on the
  passed conn. send() would open a second, independent connection and break the
  coupling: a reap announcement for a state change that never landed, or vice versa.
- _surface_stale_rr_to_coord(): post_on_conn() on the passed conn too -- NOT because
  it needs to be atomic with anything else (it's explicitly its own commit, decoupled
  from the reap decision above it), but because the EXISTING test suite
  (test_irsyad_worker_reaper_integration.py) deliberately proves a bad/closed PASSED
  conn makes this fail safe (swallowed, never raised). send() would open an unrelated
  fresh connection, silently defeating that contract -- a closed caller conn would no
  longer matter, since a fresh connection would simply succeed regardless. Reusing the
  passed conn keeps that fail-safe test meaningful.
"""
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import scripts.irsyad_worker_reaper as reaper  # noqa: E402


def test_no_raw_insert_into_agent_messages_remains():
    text = (_ROOT / "scripts" / "irsyad_worker_reaper.py").read_text()
    assert "INSERT INTO agent_messages" not in text


def test_reap_and_surface_go_through_post_on_conn():
    text = (_ROOT / "scripts" / "irsyad_worker_reaper.py").read_text()
    assert "from scripts.bus_send import post_on_conn" in text


def test_reap_updates_fleet_lanes_deletes_agent_status_and_posts_one_row(reaper_db, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO fleet_lanes (lane, desired_state) VALUES ('irsyad-worker-3', 'up')")
        cur.execute("INSERT INTO agent_status (agent_id, tmux_session) VALUES ('cc-irsyad-3', 'irsyad-worker-3')")
        conn.commit()

    with psycopg.connect(reaper_db) as conn:
        reaper._reap(conn, "cc-irsyad-3", "irsyad-worker-3", dry=False)

    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT desired_state FROM fleet_lanes WHERE lane='irsyad-worker-3'")
        assert cur.fetchone()[0] == "down"
        cur.execute("SELECT count(*) FROM agent_status WHERE tmux_session='irsyad-worker-3'")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT from_agent, to_agent, message_type, priority, requires_response, subject "
            "FROM agent_messages")
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    from_agent, to_agent, mtype, prio, req, subj = rows[0]
    assert (from_agent, to_agent, mtype, prio, req) == (
        "cc-orchestrator", "orch-console", "update", "P3", False)
    assert "REAPED" in subj and "cc-irsyad-3" in subj


def test_reap_rolls_back_fleet_lanes_and_agent_status_if_the_bus_post_fails(reaper_db, monkeypatch):
    # Proves the coupling: if post_on_conn() fails, NOTHING commits -- not the
    # fleet_lanes/agent_status changes either (console's "spawned worker X should not
    # survive if the transaction rolls back" concern, landing on the reap side).
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    import scripts.bus_send as bs

    def _boom(*a, **k):
        raise RuntimeError("bus RPC down")

    monkeypatch.setattr(bs, "post_on_conn", _boom)

    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO fleet_lanes (lane, desired_state) VALUES ('irsyad-worker-4', 'up')")
        cur.execute("INSERT INTO agent_status (agent_id, tmux_session) VALUES ('cc-irsyad-4', 'irsyad-worker-4')")
        conn.commit()

    with pytest.raises(RuntimeError):
        with psycopg.connect(reaper_db) as conn:
            reaper._reap(conn, "cc-irsyad-4", "irsyad-worker-4", dry=False)

    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT desired_state FROM fleet_lanes WHERE lane='irsyad-worker-4'")
        assert cur.fetchone()[0] == "up"     # unchanged -- never committed
        cur.execute("SELECT count(*) FROM agent_status WHERE tmux_session='irsyad-worker-4'")
        assert cur.fetchone()[0] == 1        # unchanged -- never committed
