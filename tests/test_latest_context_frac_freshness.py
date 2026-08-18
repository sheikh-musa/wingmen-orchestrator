"""op#14565 — latest_context_frac must key telemetry freshness on the last-WRITE time
(ended_at, which the auto-writer advances to the session file's mtime on EVERY upsert),
not the frozen session-start created_at.

Root cause (verified live 2026-08-18): a bloated, long-lived (un-recycled) worker lane
keeps a FROZEN created_at (session start, hours old) while the writer keeps upserting its
live latest_context_tokens + ended_at. sre_lane_recycle.latest_context_frac filtered
`created_at > now() - interval '30 minutes'` -> the row is excluded -> returns None ->
gate_context_bloat False -> "0 WOULD-RECYCLE" while the lane is actually 90%+ bloated.
That is the operator-visible "lanes still bloated, not recycling" symptom (bus #27889).

Live-DB integration test: the defect lives IN the SQL WHERE clause, so a mocked cursor
would bypass exactly the thing under test. Uses a synthetic identity + pytest source and
cleans up before and after, so it never touches a real lane's rows.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sre_lane_recycle as slr  # noqa: E402

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
integration = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")

_IDENT = "zz-pytest-op14565-frac"
_SRC = "pytest_op14565"


def _cleanup(conn):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM cc_session_costs WHERE cc_identity=%s AND source=%s",
            [_IDENT, _SRC],
        )


@pytest.fixture
def db_conn():
    conn = psycopg.connect(_DSN, autocommit=True)
    _cleanup(conn)
    try:
        yield conn
    finally:
        _cleanup(conn)
        conn.close()


def _insert_row(conn, *, created_ago: str, ended_ago: str, ctx: int):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cc_session_costs "
            "(cc_identity, session_id, started_at, ended_at, "
            " latest_context_tokens, source, created_at) "
            f"VALUES (%s,%s, now()-interval '{created_ago}', now()-interval '{ended_ago}', "
            f" %s, %s, now()-interval '{created_ago}')",
            [_IDENT, _IDENT + "-sess", ctx, _SRC],
        )


@integration
def test_bloated_lane_old_created_but_fresh_ended_reads_its_bloat(db_conn):
    """created_at 6h ago (session start), ended_at 2min ago (writer just upserted the live
    context). latest_context_frac must return the real ~0.90 bloat, NOT None — otherwise a
    genuinely-bloated long-lived lane never recycles (op#14565)."""
    _insert_row(db_conn, created_ago="6 hours", ended_ago="2 minutes", ctx=900_000)

    frac = slr.latest_context_frac(db_conn, _IDENT, window=1_000_000)

    assert frac is not None, "fresh-ended_at bloated lane must be measurable (op#14565)"
    assert 0.89 < frac < 0.91


@integration
def test_genuinely_stale_lane_still_reads_none(db_conn):
    """ended_at 90min ago = last write is older than the 30min freshness window. Must still
    read None: the fix must not turn genuinely-stale telemetry into a false bloat trigger
    (fail-closed preserved — never recycle a lane we cannot prove is CURRENTLY bloated)."""
    _insert_row(db_conn, created_ago="6 hours", ended_ago="90 minutes", ctx=900_000)

    frac = slr.latest_context_frac(db_conn, _IDENT, window=1_000_000)

    assert frac is None, "telemetry older than the freshness window must fail closed"
