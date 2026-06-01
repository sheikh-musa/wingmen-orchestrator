"""Live-DB schema test for cc_session_costs BIGINT promotion.

Per the 2026-06-02 migration: 4 token columns widened from INTEGER to BIGINT
to fix the cc-cosem cache_read overflow at 1.66B (INTEGER max 2.1B).
"""
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
pytestmark_integration = pytest.mark.skipif(not _DSN, reason="DATABASE_URL not set")


@pytestmark_integration
@pytest.mark.parametrize("col", [
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
])
def test_cc_session_costs_token_column_is_bigint(col):
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='cc_session_costs' AND column_name=%s",
            (col,),
        )
        row = cur.fetchone()
    assert row is not None, f"{col} column missing"
    assert row[0] == "bigint", f"{col} expected bigint, got {row[0]}"


@pytestmark_integration
def test_boot_briefing_view_intact_after_bigint_promotion():
    """The migration drops + recreates boot_briefing. Regression guard: arm
    count and the cc_session_costs arm specifically must survive."""
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('boot_briefing'::regclass, true)")
        defn = cur.fetchone()[0]
    assert "'cc_session_costs'::text AS source" in defn
    arm_markers = defn.count("::text AS source")
    assert arm_markers >= 20, f"boot_briefing arm count regressed: {arm_markers}"
