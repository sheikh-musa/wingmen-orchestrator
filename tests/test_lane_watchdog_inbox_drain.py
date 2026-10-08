"""Tests for lane_watchdog's idle-lane inbox drain (bus #51166 / #58271 / orch-console
#59675 regression class).

THE BUG: unread_bus_work() mapped a tmux session -> fleet_lanes.base_agent_id and
counted UNREAD agent_messages addressed to that BASE id ALONE. A lane that runs under
a base identity but is ADDRESSED by its instance/sub-tag id (adcda/platform instances
relaunch with CC_BASE_OVERRIDE=cc-cosem-adcda, so they run as the base yet register a
distinct sub-tag like 'cc-cosem-adcda-2') therefore had its instance-addressed mail
counted as ZERO. The IDLE-CLEAN nudge ("drain your inbox") never fired, and operator
rows addressed to 'cc-cosem-adcda-2' (a client export fix, an Arabic correction) sat
unread. Same shape as #51166 (cc-shipforge / cc-scholar) already fixed for the boot
arm (build_launch_context.inbox_or_filter) and the runtime reader (my_inbox.my_ids):
the inbox read must cover to_agent IN (base, instance), never base alone.

Two layers of proof:
  1. PURE (inbox_ids) — deterministic, always runs: the id set now covers the
     instance; a singleton (no distinct instance) is byte-identical to before.
  2. DB-integration (unread_bus_work) — skipped unless a LOCAL Postgres DSN is set
     (scripts/pytest_local.sh Option A); proves an instance-addressed row IS counted
     end-to-end and that the OLD base-only query would have MISSED it.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.lane_watchdog import inbox_ids


# ── Layer 1: PURE id-set logic (always runs) ─────────────────────────────────
def test_inbox_ids_covers_base_and_instance_when_distinct():
    # the exact bug-regression shape: an adcda instance running under its base
    assert inbox_ids("cc-cosem-adcda", "cc-cosem-adcda-2") == [
        "cc-cosem-adcda", "cc-cosem-adcda-2"]


def test_inbox_ids_singleton_instance_equals_base_is_base_only():
    # cc-quality / cai / any single-identity singleton: unchanged, no double-read
    assert inbox_ids("cc-quality", "cc-quality") == ["cc-quality"]


def test_inbox_ids_singleton_no_instance_is_base_only():
    # no sub-tag resolved (None) -> byte-identical to the old base-only behaviour
    assert inbox_ids("cc-quality", None) == ["cc-quality"]


def test_inbox_ids_base_first_ordering():
    # base is first so an = ANY(...) count is stable and the base is never dropped
    ids = inbox_ids("cc-cosem-platform", "cc-cosem-platform-1")
    assert ids[0] == "cc-cosem-platform"
    assert "cc-cosem-platform-1" in ids
    assert len(ids) == len(set(ids))  # no duplicates


# ── Layer 2: DB-integration (local PG only; skips otherwise) ─────────────────
def _dsn():
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — DB-integration test (scripts/pytest_local.sh Option A)")
    return dsn


def test_unread_bus_work_counts_instance_addressed_rows():
    """An idle lane with ONLY instance-addressed unread work must be counted (>0).
    Proves the fix end-to-end: the old base-only query returned 0 for this exact
    row, so the lane was never nudged to drain it."""
    import psycopg
    from nervous_system import lane_watchdog

    dsn = _dsn()
    tag = uuid.uuid4().hex[:8]
    sess = f"zz-test-lane-{tag}"
    base = f"cc-zztest-{tag}"
    instance = f"{base}-2"
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        # minimal rows the reader joins on
        cur.execute("INSERT INTO fleet_lanes (lane, base_agent_id) VALUES (%s,%s)", (sess, base))
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, status, last_heartbeat) "
            "VALUES (%s,%s,%s,'working', now())", (instance, base, sess))
        # the operator row addressed to the INSTANCE id only (the lost-mail shape)
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
            "requires_response, priority) "
            "VALUES ('orch-console',%s,'task','client export fix','x',true,'P1')", (instance,))
        try:
            # point the reader at THIS dsn regardless of .env
            os.environ["DATABASE_URL"] = dsn
            n = lane_watchdog.unread_bus_work(sess)
            assert n >= 1, (
                f"instance-addressed unread row not counted for {instance} "
                f"(base-only regression) — got {n}")

            # regression guard: the OLD base-only count IS zero for this row,
            # proving the row was genuinely instance-only and would have been missed.
            cur.execute(
                "SELECT count(*) FROM agent_messages WHERE to_agent=%s AND read_at IS NULL "
                "AND (requires_response OR priority='P1') "
                "AND created_at > now() - interval '45 minutes'", (base,))
            assert cur.fetchone()[0] == 0, "base-only query should miss the instance row"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (instance,))
            cur.execute("DELETE FROM agent_status WHERE agent_id=%s", (instance,))
            cur.execute("DELETE FROM fleet_lanes WHERE lane=%s", (sess,))


def test_unread_bus_work_singleton_unaffected():
    """A singleton lane (no distinct instance row) behaves exactly as before:
    base-addressed unread is counted, and nothing else changes."""
    import psycopg
    from nervous_system import lane_watchdog

    dsn = _dsn()
    tag = uuid.uuid4().hex[:8]
    sess = f"zz-test-singleton-{tag}"
    base = f"cc-zzsingle-{tag}"
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO fleet_lanes (lane, base_agent_id) VALUES (%s,%s)", (sess, base))
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, "
            "requires_response, priority) "
            "VALUES ('orch-console',%s,'task','base work','x',true,'P1')", (base,))
        try:
            os.environ["DATABASE_URL"] = dsn
            n = lane_watchdog.unread_bus_work(sess)
            assert n >= 1, f"base-addressed unread must still be counted — got {n}"
        finally:
            cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (base,))
            cur.execute("DELETE FROM fleet_lanes WHERE lane=%s", (sess,))
