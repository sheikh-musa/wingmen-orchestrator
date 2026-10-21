"""Regression test for the discover_lanes() lane/session mismatch bug (2026-10-19,
cc-cosem-platform-3's #71678/#71679/#71680/#71705 -- the SAME lane name 'cosem-platform-adhoc'
misrouted to the wrong sibling instance 3+ times in under an hour).

Root cause: discover_lanes()'s inner subquery picks ONE (base_agent_id, tmux_session) pair per
base -- whichever live sibling has the freshest heartbeat -- then separately LEFT JOINs an
ARBITRARY fleet_lanes row for that base (tiebroken only by worktree_path-not-null, then lane
name alphabetically). The chosen `tmux_session` and the chosen `lane` name can describe two
DIFFERENT siblings under a multi-instance base. A caller that builds nudge text from `lane` but
resolves `to_agent` from `tmux_session` (lane_proactive_recycle_nudge.py) ends up addressing the
WRONG instance with the wrong lane's content.

Fix: prefer the fleet_lanes row whose `lane` exactly equals the chosen `tmux_session` (the
convention this fleet already follows everywhere) before falling back to the old tiebreak. This
does not change discover_lanes' one-row-per-base cardinality (by design, per its own docstring) --
it only ensures that one row is internally self-consistent."""
import psycopg
import pytest

from scripts import sre_lane_recycle as slr


@pytest.fixture
def lanes_db(pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""
            CREATE TABLE agent_status (
                agent_id        text PRIMARY KEY,
                base_agent_id   text,
                tmux_session    text,
                last_heartbeat  timestamptz
            )
        """)
        cur.execute("""
            CREATE TABLE fleet_lanes (
                lane            text PRIMARY KEY,
                base_agent_id   text,
                worktree_path   text,
                branch          text,
                notes           text,
                desired_state   text
            )
        """)
    return pg_dsn


def test_returned_lane_matches_the_chosen_session_not_an_arbitrary_sibling(lanes_db, monkeypatch):
    # cc-cosem-platform has 2 live siblings. -3 (author) has the FRESHEST heartbeat right now;
    # -4 (adhoc) is slightly older but still live. Before the fix, the arbitrary fleet_lanes
    # tiebreak (alphabetical) picks 'cosem-platform-adhoc' ('a' < 'c'...'author') regardless of
    # which session was actually chosen -- reproducing the real incident exactly.
    with psycopg.connect(lanes_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, last_heartbeat) "
            "VALUES (%s, %s, %s, now() - interval '1 minute'), "
            "       (%s, %s, %s, now() - interval '5 minutes')",
            ["cc-cosem-platform-3", "cc-cosem-platform", "cosem-platform-author",
             "cc-cosem-platform-4", "cc-cosem-platform", "cosem-platform-adhoc"])
        cur.execute(
            "INSERT INTO fleet_lanes (lane, base_agent_id, worktree_path, branch, notes, desired_state) "
            "VALUES (%s, %s, %s, %s, %s, %s), (%s, %s, %s, %s, %s, %s)",
            ["cosem-platform-author", "cc-cosem-platform", "/wt/author", "b1", "", "up",
             "cosem-platform-adhoc", "cc-cosem-platform", "/wt/adhoc", "b2", "", "up"])
        conn.commit()

    monkeypatch.setattr(slr.fhb, "SINGLETON_BODIES", {"cc-nonexistent-singleton"})

    with psycopg.connect(lanes_db) as conn:
        lanes = slr.discover_lanes(conn)

    assert len(lanes) == 1
    row = lanes[0]
    # THE regression pin: the returned lane name must describe the SAME instance as the
    # returned tmux_session -- never a different sibling's lane glued onto this session.
    assert row["lane"] == row["tmux_session"], (
        f"lane {row['lane']!r} does not match its own tmux_session {row['tmux_session']!r} -- "
        f"this is the #71678/#71705 mismatch class")
    # With the freshest-heartbeat session correctly chosen, it should be -3/author, not adhoc.
    assert row["tmux_session"] == "cosem-platform-author"
    assert row["lane"] == "cosem-platform-author"


def test_single_live_sibling_still_resolves_correctly(lanes_db, monkeypatch):
    """Sanity: the common case (one live instance per base) is untouched by the fix."""
    with psycopg.connect(lanes_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, last_heartbeat) "
            "VALUES (%s, %s, %s, now())",
            ["cc-finance-1", "cc-finance", "finance"])
        cur.execute(
            "INSERT INTO fleet_lanes (lane, base_agent_id, worktree_path, branch, notes, desired_state) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ["finance", "cc-finance", "/wt/finance", "main", "", "up"])
        conn.commit()

    monkeypatch.setattr(slr.fhb, "SINGLETON_BODIES", {"cc-nonexistent-singleton"})

    with psycopg.connect(lanes_db) as conn:
        lanes = slr.discover_lanes(conn)

    assert len(lanes) == 1
    assert lanes[0]["lane"] == "finance"
    assert lanes[0]["tmux_session"] == "finance"


# ── per_instance kwarg (bus #73007/#73009/#73011) ────────────────────────────────
def test_per_instance_false_default_still_collapses_to_one_row_per_base(lanes_db, monkeypatch):
    """Default (per_instance=False) must stay byte-identical to the pre-existing behavior relied
    on by sre_lane_recycle.py's own CLI/checkpoint-recycle driver, whose gates are not yet
    sibling-aware (facet 13, deferred) -- two live siblings of the same base still collapse to the
    single freshest-heartbeat row."""
    with psycopg.connect(lanes_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, last_heartbeat) "
            "VALUES (%s, %s, %s, now() - interval '1 minute'), "
            "       (%s, %s, %s, now() - interval '5 minutes')",
            ["cc-x-1", "cc-x", "x-1", "cc-x-3", "cc-x", "x-3"])
        cur.execute(
            "INSERT INTO fleet_lanes (lane, base_agent_id, worktree_path, branch, notes, desired_state) "
            "VALUES (%s, %s, %s, %s, %s, %s), (%s, %s, %s, %s, %s, %s)",
            ["x-1", "cc-x", "/wt/x1", "b1", "", "up",
             "x-3", "cc-x", "/wt/x3", "b2", "", "up"])
        conn.commit()

    monkeypatch.setattr(slr.fhb, "SINGLETON_BODIES", {"cc-nonexistent-singleton"})

    with psycopg.connect(lanes_db) as conn:
        lanes = slr.discover_lanes(conn)

    assert len(lanes) == 1
    assert lanes[0]["tmux_session"] == "x-1"  # freshest heartbeat


def test_per_instance_true_returns_every_live_sibling(lanes_db, monkeypatch):
    """per_instance=True (used only by the two message-only sweepers, lane_recycle_deadman.py and
    lane_proactive_recycle_nudge.py) must return ALL live sessions for a multi-sibling base, not
    collapse them -- this is what lets state_key_for() disambiguate siblings in the same sweep."""
    with psycopg.connect(lanes_db) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, last_heartbeat) "
            "VALUES (%s, %s, %s, now() - interval '1 minute'), "
            "       (%s, %s, %s, now() - interval '5 minutes')",
            ["cc-x-1", "cc-x", "x-1", "cc-x-3", "cc-x", "x-3"])
        cur.execute(
            "INSERT INTO fleet_lanes (lane, base_agent_id, worktree_path, branch, notes, desired_state) "
            "VALUES (%s, %s, %s, %s, %s, %s), (%s, %s, %s, %s, %s, %s)",
            ["x-1", "cc-x", "/wt/x1", "b1", "", "up",
             "x-3", "cc-x", "/wt/x3", "b2", "", "up"])
        conn.commit()

    monkeypatch.setattr(slr.fhb, "SINGLETON_BODIES", {"cc-nonexistent-singleton"})

    with psycopg.connect(lanes_db) as conn:
        lanes = slr.discover_lanes(conn, per_instance=True)

    assert len(lanes) == 2
    sessions = {row["tmux_session"] for row in lanes}
    assert sessions == {"x-1", "x-3"}
    for row in lanes:
        assert row["base_agent_id"] == "cc-x"
        assert row["lane"] == row["tmux_session"]
