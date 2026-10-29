"""scripts/lib/reset_auditor_arm_verify.py -- the cc-quality self-arm-row
check/consume helper for reset_auditor.sh (Musa op_msg 35891, ask 5849;
orch-console #83292/#83304; cc-quality's own review points in #83301).

Runs against the ephemeral agent_messages schema (tests/conftest.py's
reaper_db fixture) -- never touches DATABASE_URL or any live silo.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))
from reset_auditor_arm_verify import check, consume, MARKER_PREFIX  # noqa: E402

NOW = datetime.now(timezone.utc)


def _row(cur, from_agent="cc-quality", to_agent="orch-console", message_type="decision",
         subject=f"{MARKER_PREFIX} reset please", created_at=NOW, responded_at=None) -> int:
    cur.execute(
        "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
        "priority, created_at, responded_at) VALUES (%s,%s,%s,%s,'x','P1',%s,%s) RETURNING id",
        (from_agent, to_agent, message_type, subject, created_at, responded_at))
    return cur.fetchone()[0]


# ── check(): read-only, never writes ─────────────────────────────────────────
def test_check_passes_a_valid_row_and_does_not_consume_it(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur)
        ok, reason = check(conn, row_id, "quality")
        assert ok, reason
        cur.execute("SELECT responded_at FROM agent_messages WHERE id=%s", (row_id,))
        assert cur.fetchone()[0] is None, "check() must never write responded_at"


def test_check_refuses_no_such_row(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn:
        ok, reason = check(conn, 999999, "quality")
        assert not ok
        assert "does not exist" in reason


def test_check_refuses_a_stale_row(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur, created_at=NOW - timedelta(minutes=31))
        ok, reason = check(conn, row_id, "quality")
        assert not ok
        assert "older than" in reason


def test_check_refuses_a_row_from_another_agent(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur, from_agent="cc-storefront")
        ok, reason = check(conn, row_id, "quality")
        assert not ok
        assert "not cc-quality" in reason


def test_check_refuses_an_already_consumed_row(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur, responded_at=NOW - timedelta(minutes=1))
        ok, reason = check(conn, row_id, "quality")
        assert not ok
        assert "already consumed" in reason


def test_check_refuses_a_row_without_the_marker(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur, subject="please reset me")
        ok, reason = check(conn, row_id, "quality")
        assert not ok
        assert "marker" in reason


def test_check_refuses_scope_outside_quality_even_with_a_valid_looking_row(reaper_db):
    """storefront/finance must never pass, even with a perfectly valid row --
    this path is scoped to cc-quality only (console spec item 1)."""
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur)
        for target in ("storefront", "finance"):
            ok, reason = check(conn, row_id, target)
            assert not ok, f"{target} must be refused"
            assert "scoped to cc-quality only" in reason


# ── consume(): the atomic single-use write ───────────────────────────────────
def test_consume_passes_a_valid_row_and_marks_it_responded(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur)
        ok, reason = consume(conn, row_id, "quality")
        assert ok, reason
        cur.execute("SELECT responded_at FROM agent_messages WHERE id=%s", (row_id,))
        assert cur.fetchone()[0] is not None


def test_consume_refuses_a_replayed_row(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur)
        ok1, _ = consume(conn, row_id, "quality")
        assert ok1
        ok2, reason2 = consume(conn, row_id, "quality")
        assert not ok2
        assert "already consumed" in reason2


def test_consume_refuses_scope_outside_quality(reaper_db):
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur)
        ok, reason = consume(conn, row_id, "storefront")
        assert not ok
        assert "scoped to cc-quality only" in reason
        cur.execute("SELECT responded_at FROM agent_messages WHERE id=%s", (row_id,))
        assert cur.fetchone()[0] is None, "a scope refusal must not consume the row"


def test_check_then_consume_both_succeed_on_a_fresh_row(reaper_db):
    """The normal two-call sequence: check at the gate, consume right before
    the audit row -- both see the same still-unconsumed row."""
    with psycopg.connect(reaper_db, autocommit=True) as conn, conn.cursor() as cur:
        row_id = _row(cur)
        ok1, _ = check(conn, row_id, "quality")
        assert ok1
        ok2, _ = consume(conn, row_id, "quality")
        assert ok2
