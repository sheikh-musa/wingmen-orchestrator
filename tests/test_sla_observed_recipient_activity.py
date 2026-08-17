"""Migration 054: the SLA 'unresponded' violation must fire only when the recipient
is NOT observably active since the request — the RED-on-absence fix (orch-console
#24803, concurred #24817). The false-P0 class paged the operator's phone because the
view keyed on `responded_at IS NULL`, which nobody sets; a live recipient working its
bus is not "nobody's home".

The unification: a shared `agent_observed_activity(agent_id, last_observed_at)` view
that BOTH this SLA view AND the CAI-1029 commitment sweeper consume (Nazim moves the
sweeper onto it — one signal, no twin).

These tests apply the ACTUAL migration file in a rolled-back txn (gate-test =
shipped-path) and pin the two properties that matter, especially the second:
  * a live recipient (posted anything after the request) is SUPPRESSED, and
  * a DEAD/silent recipient sitting on a real request STILL FLAGS (no-suppression —
    the property whose failure would be worse than the bug we are fixing).
"""
import os

import psycopg2
import pytest

_MIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "migrations", "054_sla_observed_recipient_activity.sql")


# agent ids used as from_agent/to_agent across the tests (agent_messages FKs -> agents)
_TEST_AGENTS = ("_sre_snd", "_sre_rcp_active", "_sre_rcp_dead", "_sre_rcp_prior",
                "_sre_rcp_unread", "_sre_obs", "x")


@pytest.fixture
def cur():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.autocommit = False
    c = conn.cursor()
    with open(_MIG) as f:
        c.execute(f.read())  # apply the migration inside the rolled-back txn
    for aid in _TEST_AGENTS:  # satisfy the agent_messages -> agents FK (rolled back)
        c.execute("INSERT INTO agents (id, display_name) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING",
                  (aid, aid))
    yield c
    conn.rollback()
    conn.close()


def _seed_unresponded(cur, sender, recip, *, priority="P1", age_min=1440):
    """A read, requires_response, unstamped message old enough to breach any
    unresponded threshold."""
    cur.execute(
        """INSERT INTO agent_messages
             (from_agent, to_agent, message_type, subject, body, priority,
              requires_response, created_at, read_at)
           VALUES (%s,%s,'question','t','t',%s,true,
                   now() - (%s || ' minutes')::interval, now())
           RETURNING id""",
        (sender, recip, priority, age_min),
    )
    return cur.fetchone()[0]


def _flagged_unresponded(cur, mid):
    cur.execute(
        "SELECT 1 FROM inbox_sla_violations WHERE message_id=%s AND violation_type='unresponded'",
        (mid,),
    )
    return cur.fetchone() is not None


def test_active_recipient_suppresses_the_false_unresponded_page(cur):
    """The bug: recipient answered on the bus (a new row) but never stamped
    responded_at -> used to page P0. Now: any bus row FROM the recipient after the
    request suppresses it."""
    mid = _seed_unresponded(cur, "_sre_snd", "_sre_rcp_active")
    cur.execute(
        """INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, priority, created_at)
           VALUES ('_sre_rcp_active','_sre_snd','update','re','x','P3', now())"""
    )
    assert not _flagged_unresponded(cur, mid), "an observably-active recipient must not page"


def test_dead_recipient_still_flags_the_genuine_stall(cur):
    """THE NO-SUPPRESSION PROPERTY: a recipient that did NOTHING since the request is
    a real stall and MUST still flag. If this ever fails, the fix has blinded the
    watchdog — worse than the false pages it removes."""
    mid = _seed_unresponded(cur, "_sre_snd", "_sre_rcp_dead")
    assert _flagged_unresponded(cur, mid), "a silent/dead recipient sitting on a read request must still flag"


def test_activity_before_the_request_does_not_suppress(cur):
    """Only activity AFTER the request counts — a recipient active earlier then gone
    silent is still a stall."""
    cur.execute(
        """INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, priority, created_at)
           VALUES ('_sre_rcp_prior','_sre_snd','update','old','x','P3', now() - interval '2 days')"""
    )
    mid = _seed_unresponded(cur, "_sre_snd", "_sre_rcp_prior", age_min=1440)
    assert _flagged_unresponded(cur, mid), "activity BEFORE the request must not suppress"


def test_unread_branch_unchanged_still_pages(cur):
    """The 'unread' branch is untouched — an unread message still violates regardless
    of recipient activity (defensive: the fix is scoped to 'unresponded')."""
    cur.execute(
        """INSERT INTO agent_messages
             (from_agent, to_agent, message_type, subject, body, priority, requires_response, created_at, read_at)
           VALUES ('_sre_snd','_sre_rcp_unread','question','t','t','P1',true,
                   now() - interval '1 day', NULL) RETURNING id"""
    )
    mid = cur.fetchone()[0]
    cur.execute(
        "SELECT 1 FROM inbox_sla_violations WHERE message_id=%s AND violation_type='unread'", (mid,)
    )
    assert cur.fetchone() is not None


def test_agent_observed_activity_exposes_last_bus_row(cur):
    """The shared oracle view Nazim's sweeper will consume: last_observed_at = the
    agent's most recent bus row."""
    cur.execute(
        """INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, body, priority, created_at)
           VALUES ('_sre_obs','x','update','a','x','P3', now() - interval '5 minutes')"""
    )
    cur.execute("SELECT last_observed_at FROM agent_observed_activity WHERE agent_id='_sre_obs'")
    row = cur.fetchone()
    assert row is not None and row[0] is not None
