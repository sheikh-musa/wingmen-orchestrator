"""Integration tests for Batch 2 BUG-030 bridge trigger fix.

Covers:
  - parent_msg_id FK column + constraint
  - announce_to_agent + announce_thread_id override columns
  - trigger_cai_decision_announce 3-tier routing (explicit > inferred > legacy)
  - UPDATE-path firing on challenge_status change (BUG-020 precedent)

Uses psycopg against the live orchestrator Supabase. Each test manages its
own SAVEPOINT/ROLLBACK or DELETE cleanup to avoid leaking test rows.
"""
import os
import uuid

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

pytestmark = pytest.mark.skipif(
    not DSN, reason="DATABASE_URL not set — integration tests skipped"
)


def _conn():
    return psycopg.connect(DSN, autocommit=False)


def _cleanup(ref_prefix):
    """Delete any test rows matching a decision_ref prefix. Crash-safe."""
    with psycopg.connect(DSN, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_messages WHERE subject LIKE %s",
                (f"{ref_prefix}%",),
            )
            cur.execute(
                "DELETE FROM strategic_decisions WHERE decision_ref LIKE %s",
                (f"{ref_prefix}%",),
            )


@pytest.fixture(autouse=True)
def _clean_test_bug030_rows():
    _cleanup("TEST-BUG030-")
    yield
    _cleanup("TEST-BUG030-")
