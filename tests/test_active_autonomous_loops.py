"""Live-DB tests for CAI-RESP-157 [A] — active_autonomous_loops boot_briefing arm.

Schema, view exposure, upsert/delete semantics. Filesystem-scan logic is
unit-tested separately in test_autonomous_loop_detector.py (next task).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set — skipping Supabase integration tests"
)


@pytestmark_integration
def test_active_autonomous_loops_table_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='active_autonomous_loops' ORDER BY ordinal_position"
            )
            cols = {r[0]: r[1] for r in cur.fetchall()}
    assert cols, "active_autonomous_loops table missing"
    assert cols["cc_identity"] == "text"
    assert cols["last_fire_at"] == "timestamp with time zone"
    assert cols["sessions_24h"] == "integer"
    assert "cadence_seconds" in cols
    assert cols["detected_at"] == "timestamp with time zone"


@pytestmark_integration
def test_boot_briefing_view_has_active_autonomous_loops_arm():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    assert "active_autonomous_loops" in defn, \
        "boot_briefing view missing active_autonomous_loops UNION arm"


@pytestmark_integration
def test_boot_briefing_view_row_when_table_populated():
    """Insert a fixture row; verify it surfaces in boot_briefing. Cleanup after."""
    test_cc = "cc-scholar-test-fixture"
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO active_autonomous_loops "
                    "(cc_identity, last_fire_at, sessions_24h, cadence_seconds) "
                    "VALUES (%s, now(), 299, 300) "
                    "ON CONFLICT (cc_identity) DO UPDATE SET "
                    "  last_fire_at=EXCLUDED.last_fire_at, "
                    "  sessions_24h=EXCLUDED.sessions_24h, "
                    "  cadence_seconds=EXCLUDED.cadence_seconds",
                    (test_cc,),
                )
                cur.execute(
                    "SELECT context FROM boot_briefing "
                    "WHERE source='active_autonomous_loops' AND key=%s",
                    (test_cc,),
                )
                r = cur.fetchone()
                assert r is not None, f"boot_briefing should expose fixture row for {test_cc}"
                ctx = r[0]
                assert ctx["sessions_24h"] == 299
                assert ctx["cadence_seconds"] == 300
            finally:
                cur.execute("DELETE FROM active_autonomous_loops WHERE cc_identity=%s", (test_cc,))
