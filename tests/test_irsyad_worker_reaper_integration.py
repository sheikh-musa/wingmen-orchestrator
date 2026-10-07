"""Integration proof for the DB-querying half of scripts/irsyad_worker_reaper.py, against
a real (ephemeral) Postgres rather than mocks (bus #58159 item #339 fast-follow: PR #339
only unit-tested the pure should_reap()/is_worker_session() logic; _has_pending_order()
itself -- the function that actually queries agent_messages/coord_dispatch_queue -- had
no test exercising a real connection).

Also covers bus #58316's reaper-surfaces-stale-rr-skips-to-coord addition: a SKIP caused
solely by a requires_response row older than 24h gets ONE deduped bus post to
cc-irsyad-coord; the reap decision itself never changes with row age.
"""
import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.irsyad_worker_reaper import (  # noqa: E402
    _already_surfaced_recently,
    _has_pending_order,
    _stale_rr_only_skip,
    _surface_stale_rr_to_coord,
)


# ── _has_pending_order ──────────────────────────────────────────────────────────────
def test_has_pending_order_false_with_no_rows(reaper_db):
    with psycopg.connect(reaper_db) as conn:
        assert _has_pending_order(conn, "cc-irsyad-3") is False


def test_has_pending_order_true_for_unresponded_requires_response_row(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority) VALUES ('orch-console','cc-irsyad-3','question',"
            "'s','b',true,'P2')")
        conn.commit()
        assert _has_pending_order(conn, "cc-irsyad-3") is True


def test_has_pending_order_false_once_responded(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority,responded_at) VALUES ('orch-console','cc-irsyad-3',"
            "'question','s','b',true,'P2',now())")
        conn.commit()
        assert _has_pending_order(conn, "cc-irsyad-3") is False


def test_has_pending_order_true_for_claimed_undone_dispatch_row(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO coord_dispatch_queue (title,claimed_by) VALUES ('t','cc-irsyad-3')")
        conn.commit()
        assert _has_pending_order(conn, "cc-irsyad-3") is True


def test_has_pending_order_false_for_done_dispatch_row(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO coord_dispatch_queue (title,claimed_by,done_at) "
            "VALUES ('t','cc-irsyad-3',now())")
        conn.commit()
        assert _has_pending_order(conn, "cc-irsyad-3") is False


def test_has_pending_order_forced_connection_error_fails_safe_to_true(reaper_db):
    # the module's own contract (docstring): "Fail-safe: a query error => treat as
    # pending (never reap on ambiguity)" -- orch-console #58489 flagged this as the
    # one case missing from the first pass.
    with psycopg.connect(reaper_db) as conn:
        conn.close()
        assert _has_pending_order(conn, "cc-irsyad-3") is True


def test_has_pending_order_ignores_rows_to_other_agents(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority) VALUES ('orch-console','cc-irsyad-9','question',"
            "'s','b',true,'P2')")
        conn.commit()
        assert _has_pending_order(conn, "cc-irsyad-3") is False


# ── _stale_rr_only_skip (bus #58316) ────────────────────────────────────────────────
def test_stale_rr_only_skip_empty_when_no_rows(reaper_db):
    with psycopg.connect(reaper_db) as conn:
        assert _stale_rr_only_skip(conn, "cc-irsyad-3") == []


def test_stale_rr_only_skip_ignores_fresh_unresponded_row(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority,created_at) VALUES ('orch-console','cc-irsyad-3',"
            "'question','s','b',true,'P2',now())")
        conn.commit()
        assert _stale_rr_only_skip(conn, "cc-irsyad-3") == []


def test_stale_rr_only_skip_returns_row_older_than_24h(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority,created_at) VALUES ('orch-console','cc-irsyad-3',"
            "'question','s','b',true,'P2',now() - interval '25 hours') RETURNING id")
        stale_id = cur.fetchone()[0]
        conn.commit()
        assert _stale_rr_only_skip(conn, "cc-irsyad-3") == [stale_id]


def test_stale_rr_only_skip_ignores_already_responded_row(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            "requires_response,priority,created_at,responded_at) VALUES ('orch-console',"
            "'cc-irsyad-3','question','s','b',true,'P2',now() - interval '25 hours',now())")
        conn.commit()
        assert _stale_rr_only_skip(conn, "cc-irsyad-3") == []


# ── _already_surfaced_recently + _surface_stale_rr_to_coord dedup (bus #58316) ──────
def test_already_surfaced_recently_false_before_any_surface(reaper_db):
    with psycopg.connect(reaper_db) as conn:
        assert _already_surfaced_recently(conn, "cc-irsyad-3") is False


def test_surface_then_dedup_blocks_a_second_surface_within_24h(reaper_db):
    with psycopg.connect(reaper_db) as conn:
        _surface_stale_rr_to_coord(conn, "cc-irsyad-3", "irsyad-worker-3", [101, 102])
        assert _already_surfaced_recently(conn, "cc-irsyad-3") is True
        # a different worker's dedup key is independent
        assert _already_surfaced_recently(conn, "cc-irsyad-9") is False


def test_surface_posts_exactly_one_row_to_coord_with_the_row_ids(reaper_db):
    with psycopg.connect(reaper_db) as conn, conn.cursor() as cur:
        _surface_stale_rr_to_coord(conn, "cc-irsyad-3", "irsyad-worker-3", [101, 102])
        cur.execute(
            "SELECT from_agent, to_agent, requires_response, body FROM agent_messages "
            "WHERE to_agent='cc-irsyad-coord'")
        rows = cur.fetchall()
        assert len(rows) == 1
        from_agent, to_agent, requires_response, body = rows[0]
        assert from_agent == "cc-orchestrator"
        assert to_agent == "cc-irsyad-coord"
        assert requires_response is False  # surface-only nudge, never a blocking order itself
        assert "101" in body and "102" in body


def test_stale_rr_check_failure_fails_safe_to_empty(reaper_db, monkeypatch):
    # a broken connection must never crash the reaper loop over a nudge-only feature
    with psycopg.connect(reaper_db) as conn:
        conn.close()
        assert _stale_rr_only_skip(conn, "cc-irsyad-3") == []


def test_already_surfaced_recently_does_not_collide_on_id_prefix(reaper_db):
    # cc-quality #58499 finding 1: an unanchored LIKE '...{agent_id}%' let a shorter id
    # falsely match a longer sibling's surfaced row (cc-irsyad-3 vs cc-irsyad-31)
    with psycopg.connect(reaper_db) as conn:
        _surface_stale_rr_to_coord(conn, "cc-irsyad-31", "irsyad-worker-31", [201])
        assert _already_surfaced_recently(conn, "cc-irsyad-31") is True
        assert _already_surfaced_recently(conn, "cc-irsyad-3") is False


def test_already_surfaced_recently_fails_safe_to_true_on_connection_error(reaper_db):
    # cc-quality #58499 finding 2: a query error here must suppress this tick's nudge,
    # not crash the reaper loop
    with psycopg.connect(reaper_db) as conn:
        conn.close()
        assert _already_surfaced_recently(conn, "cc-irsyad-3") is True


def test_surface_stale_rr_to_coord_fails_safe_on_connection_error(reaper_db):
    # cc-quality #58499 finding 2: a failed insert must not raise -- the reap decision
    # above this call has already run and is unaffected by this nudge-only failure
    with psycopg.connect(reaper_db) as conn:
        conn.close()
        _surface_stale_rr_to_coord(conn, "cc-irsyad-3", "irsyad-worker-3", [101])  # must not raise
