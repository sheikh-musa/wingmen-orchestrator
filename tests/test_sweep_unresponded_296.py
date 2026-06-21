"""CAI-RESP-296: the orch sweep + agent_boot must surface read-but-UNRESPONDED
requires_response asks (not just unread), and a genuine reply must close the
loop via responded_at.

Integration tests — hit the real DB (DATABASE_URL) with an isolated test agent
(cc-test-sweep296); all test agent_messages are purged before AND after each
test (crash-safe), mirroring tests/test_auto_agent_id.py.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
# .env is gitignored (absent from a worktree); load the canonical orchestrator
# .env, falling back to the worktree-local one + default search.
load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
load_dotenv(ROOT / ".env")
load_dotenv()

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
TEST_AGENT = "cc-test-sweep296"

pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL not set — skipping integration tests")

# The exact count query scheduled_cc_sweep.sh uses for the spawn trigger.
UNRESPONDED_COUNT_SQL = (
    "SELECT count(*) FROM agent_messages "
    "WHERE to_agent=%s AND requires_response=true AND responded_at IS NULL"
)


def _conn():
    import psycopg

    return psycopg.connect(DSN, autocommit=True)


@pytest.fixture(autouse=True)
def _isolated_test_agent():
    """Ensure the test agent exists (FK target for agent_messages.to_agent) and
    purge its messages before + after each test. Never touches real rows."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO agents (id, display_name, status) VALUES (%s, %s, 'offline') "
            "ON CONFLICT (id) DO NOTHING",
            (TEST_AGENT, "sweep296 test agent"),
        )
        cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (TEST_AGENT,))
    yield
    with _conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM agent_messages WHERE to_agent=%s", (TEST_AGENT,))


def _insert_msg_fixed(read: bool, requires_response: bool, responded: bool) -> int:
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages "
            "(from_agent,to_agent,message_type,subject,body,requires_response,priority,read_at,responded_at) "
            "VALUES (%s,%s,'question','sweep296 test','b',%s,'P2',%s,%s) RETURNING id",
            (
                TEST_AGENT,
                TEST_AGENT,
                requires_response,
                now if read else None,
                now if responded else None,
            ),
        )
        return cur.fetchone()[0]


def _unresponded_count() -> int:
    with _conn() as c, c.cursor() as cur:
        cur.execute(UNRESPONDED_COUNT_SQL, (TEST_AGENT,))
        return cur.fetchone()[0]


# ── Part (a): the spawn trigger surfaces read-but-unanswered asks ──────────────

def test_read_but_unanswered_requires_response_is_counted_unresponded():
    """The exact bug: a requires_response item READ but not ANSWERED used to
    drop out of the unread-keyed trigger. It must now count as unresponded."""
    mid = _insert_msg_fixed(read=True, requires_response=True, responded=False)
    assert _unresponded_count() >= 1, "read+unanswered requires_response must trigger spawn"
    # and it's specifically our message
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT requires_response, read_at IS NOT NULL, responded_at IS NULL "
            "FROM agent_messages WHERE id=%s",
            (mid,),
        )
        req, is_read, unresp = cur.fetchone()
    assert req and is_read and unresp


def test_answered_item_does_not_count():
    """A requires_response item that's been genuinely answered (responded_at set)
    must NOT keep triggering."""
    _insert_msg_fixed(read=True, requires_response=True, responded=True)
    assert _unresponded_count() == 0


def test_non_requires_response_does_not_count():
    """An FYI (requires_response=false) is closed by read_at, not responded_at —
    it must not appear in the unresponded trigger."""
    _insert_msg_fixed(read=True, requires_response=False, responded=False)
    assert _unresponded_count() == 0


# ── Part (a): agent_boot inbox view surfaces it ────────────────────────────────

def test_agent_boot_inbox_filter_surfaces_unresponded():
    """The boot inbox view (agent_boot._inbox_or_filter) must surface a
    read-but-unresponded requires_response ask. The postgREST filter is a DNF
    encoding of this predicate; we assert (1) the equivalent SQL predicate
    surfaces the row and (2) the filter string encodes the same terms. (The
    live postgREST endpoint isn't network-reachable from CI, so we verify the
    predicate at the DB layer where the same WHERE clause runs.)"""
    from scripts import agent_boot

    mid = _insert_msg_fixed(read=True, requires_response=True, responded=False)
    # Equivalent SQL to the DNF postgREST filter.
    sql = (
        "SELECT id FROM agent_messages "
        "WHERE (to_agent=%s OR to_agent IS NULL) "
        "AND (read_at IS NULL OR (requires_response=true AND responded_at IS NULL))"
    )
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, (TEST_AGENT,))
        ids = {r[0] for r in cur.fetchall()}
    assert mid in ids, "read-but-unresponded ask must appear in the boot inbox view"

    # The actual postgREST filter encodes the same predicate (read_at OR unresponded).
    f = agent_boot._inbox_or_filter(TEST_AGENT)
    assert "read_at.is.null" in f
    assert "requires_response.eq.true" in f and "responded_at.is.null" in f
    assert TEST_AGENT in f


# ── Part (b): a genuine reply closes the loop (mark_responded) ─────────────────

def test_mark_responded_closes_loop_and_is_idempotent():
    from scripts import agent_boot

    mid = _insert_msg_fixed(read=True, requires_response=True, responded=False)
    assert _unresponded_count() >= 1

    first = agent_boot.mark_responded(mid)
    assert first is True, "first mark_responded must set responded_at"
    assert _unresponded_count() == 0, "after a genuine reply, it must not resurface"

    second = agent_boot.mark_responded(mid)
    assert second is False, "mark_responded must be idempotent (no overwrite)"


# ── Sweep script stays valid bash ──────────────────────────────────────────────

def test_scheduled_sweep_script_is_valid_bash():
    script = ROOT / "scripts" / "scheduled_cc_sweep.sh"
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, f"bash -n failed: {r.stderr}"
