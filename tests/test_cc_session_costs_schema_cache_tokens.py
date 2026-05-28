"""Live-DB schema test for cc_session_costs cache token columns."""
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
def test_cache_creation_input_tokens_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT data_type, column_default FROM information_schema.columns "
            "WHERE table_name='cc_session_costs' AND column_name='cache_creation_input_tokens'"
        )
        r = cur.fetchone()
    assert r is not None, "cache_creation_input_tokens missing"
    assert r[0] == "integer"


@pytestmark_integration
def test_cache_read_input_tokens_column_exists():
    with psycopg.connect(_DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT data_type, column_default FROM information_schema.columns "
            "WHERE table_name='cc_session_costs' AND column_name='cache_read_input_tokens'"
        )
        r = cur.fetchone()
    assert r is not None, "cache_read_input_tokens missing"
    assert r[0] == "integer"
