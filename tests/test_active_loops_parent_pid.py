"""Live-DB schema test for active_autonomous_loops.parent_pid column."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
_DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
pytestmark_integration = pytest.mark.skipif(
    not _DSN, reason="DATABASE_URL not set"
)


@pytestmark_integration
def test_parent_pid_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name='active_autonomous_loops' AND column_name='parent_pid'"
            )
            r = cur.fetchone()
    assert r is not None, "parent_pid column missing"
    assert r[0] == "integer"
    assert r[1] == "YES"


@pytestmark_integration
def test_boot_briefing_view_exposes_parent_pid():
    """active_autonomous_loops arm of boot_briefing view should now include parent_pid."""
    with psycopg.connect(_DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
            defn = cur.fetchone()[0]
    arm_start = defn.find("'active_autonomous_loops'::text")
    assert arm_start >= 0, "active_autonomous_loops arm missing in view"
    arm_end = defn.find("UNION ALL", arm_start)
    if arm_end < 0:
        arm_end = len(defn)
    arm_body = defn[arm_start:arm_end]
    assert "parent_pid" in arm_body, f"parent_pid not surfaced in active_autonomous_loops arm: {arm_body[:300]}"
