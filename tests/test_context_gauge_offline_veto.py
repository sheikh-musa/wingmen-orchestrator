"""Build B slice 3 (cc-fleet-health spec, bus #73041) -- the 11th-facet incident: a retired
agent's last cc_session_costs row kept looking fresh enough to page/auto-recycle long after it
went offline (a real retired cc-cosem-platform-4 was paged post-retirement). read_context_gauge()
had zero join against agent_status, so a stale row for an identity no longer registered as live
anywhere classified and fired exactly like a real body's.

Fix: read_context_gauge() vetoes (excludes outright) any cc_identity with no live
(non-offline, recently-heartbeating) agent_status row. auto_recycle_on_bloat.py's two call sites
(_fresh_pct, run_stage0) both call read_context_gauge() directly with no DB read of their own, so
fixing at the source covers both fire paths in one place -- confirmed by reading both call sites.

Deliberately NARROWER than facet 13 (per-sibling attribution, deferred separately): this is a
pure base-level existence check. A multi-sibling base with >=1 live sibling in agent_status still
passes, even though cc_session_costs.cc_identity is written as the bare base for every sibling.
"""
import psycopg
import pytest

from scripts import context_health_watchdog as w


@pytest.fixture
def gauge_db(pg_dsn, monkeypatch):
    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("""
            CREATE TABLE agent_status (
                agent_id        text PRIMARY KEY,
                base_agent_id   text,
                tmux_session    text,
                status          text,
                last_heartbeat  timestamptz
            )
        """)
        cur.execute("""
            CREATE TABLE cc_session_costs (
                id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                cc_identity            text,
                latest_context_tokens  bigint,
                created_at             timestamptz,
                ended_at               timestamptz
            )
        """)
    monkeypatch.setattr(w, "_dsn", lambda: pg_dsn)
    return pg_dsn


def _insert_cost_row(dsn, ident, tokens, created_at_sql="now()"):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO cc_session_costs (cc_identity, latest_context_tokens, created_at) "
            f"VALUES (%s, %s, {created_at_sql})",
            [ident, tokens])
        conn.commit()


def _insert_agent_status(dsn, agent_id, base_agent_id, tmux_session, status, heartbeat_sql="now()"):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO agent_status (agent_id, base_agent_id, tmux_session, status, last_heartbeat) "
            f"VALUES (%s, %s, %s, %s, {heartbeat_sql})",
            [agent_id, base_agent_id, tmux_session, status])
        conn.commit()


# ── 1. the exact 11th-facet incident repro ───────────────────────────────────────
def test_retired_agent_with_a_fresh_looking_stale_row_is_vetoed(gauge_db):
    # No agent_status row at all for this identity -- it has been fully retired/removed,
    # exactly like the real cc-cosem-platform-4 incident. The cc_session_costs row is
    # recent (age_s small), so pre-fix this would classify and could fire.
    _insert_cost_row(gauge_db, "cc-cosem-platform-4", 950_000)

    rows = w.read_context_gauge()

    assert [r.agent for r in rows] == []


# ── 2. multi-sibling base with normal live agent_status rows -> NOT vetoed ──────
def test_multi_sibling_base_with_a_live_sibling_is_not_vetoed(gauge_db):
    _insert_agent_status(gauge_db, "cc-x-1", "cc-x", "x-1", "working")
    _insert_agent_status(gauge_db, "cc-x-3", "cc-x", "x-3", "idle")
    # cc_session_costs.cc_identity is written as the bare base for every sibling (established
    # fact, see test_nudge_gauge_sibling_ambiguity.py) -- this must still pass through.
    _insert_cost_row(gauge_db, "cc-x", 100_000)

    rows = w.read_context_gauge()

    assert [r.agent for r in rows] == ["cc-x"]


# ── 3. base never registered in agent_status at all (bad data/typo) -> vetoed ──
def test_base_never_in_agent_status_is_vetoed_not_crashed(gauge_db):
    _insert_cost_row(gauge_db, "cc-typo-nonexistent", 500_000)

    rows = w.read_context_gauge()

    assert [r.agent for r in rows] == []


# ── 4. agent_status query itself failing -> fails SAFE (never silently vetoes everyone) ──
def test_agent_status_query_failure_disables_the_veto_not_widens_it(gauge_db):
    _insert_cost_row(gauge_db, "cc-x", 100_000)
    with psycopg.connect(gauge_db) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE agent_status")
        conn.commit()

    rows = w.read_context_gauge()

    # The liveness query raised (no such table). A DB hiccup here must degrade to the
    # pre-fix behavior for this run -- NOT to "nobody is live", which would silently
    # suppress every real page fleet-wide.
    assert [r.agent for r in rows] == ["cc-x"]


# ── 5. green-band rows unaffected either way ─────────────────────────────────────
def test_green_band_live_agent_is_classified_normally(gauge_db):
    _insert_agent_status(gauge_db, "cc-finance-1", "cc-finance", "finance", "idle")
    _insert_cost_row(gauge_db, "cc-finance", 50_000)

    rows = w.read_context_gauge()

    assert len(rows) == 1
    assert rows[0].agent == "cc-finance"
    assert rows[0].level == "green"
    assert rows[0].action == "ok"


def test_green_band_retired_agent_is_still_vetoed(gauge_db):
    # A low/green reading does not exempt a retired body from the veto -- there is simply
    # nothing left to monitor for it, regardless of what the stale number says.
    _insert_cost_row(gauge_db, "cc-retired-green", 10_000)

    rows = w.read_context_gauge()

    assert [r.agent for r in rows] == []
