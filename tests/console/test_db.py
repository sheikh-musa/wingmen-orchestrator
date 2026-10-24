"""Read-only DB layer (CAI-RESP-264 condition 2).

Reads via CONSOLE_DB_URL (a SELECT-only role DSN). For local dev it may fall
back to DATABASE_URL, but the code must NEVER read the Supabase service key and
must NEVER attempt a write.
"""
import os

import pytest

from nervous_system.console import db


def test_dsn_prefers_console_db_url(monkeypatch):
    monkeypatch.setenv("CONSOLE_DB_URL", "postgresql://ro@h/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://rw@h/db")
    assert db.resolve_dsn() == "postgresql://ro@h/db"


def test_dsn_falls_back_to_database_url(monkeypatch):
    monkeypatch.delenv("CONSOLE_DB_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://rw@h/db")
    assert db.resolve_dsn() == "postgresql://rw@h/db"


def test_dsn_none_when_unset(monkeypatch):
    monkeypatch.delenv("CONSOLE_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.resolve_dsn() is None


def test_never_reads_service_key(monkeypatch):
    """The module must not touch SUPABASE_SERVICE_KEY at all."""
    sentinel = "SHOULD-NEVER-BE-READ"
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", sentinel)
    monkeypatch.setenv("CONSOLE_DB_URL", "postgresql://ro@h/db")
    # resolve_dsn must not return the service key.
    assert sentinel not in (db.resolve_dsn() or "")


def test_messages_query_is_select_only():
    """The generated messages SQL must be a pure SELECT (no writes)."""
    sql, params = db.build_messages_query(limit=10, thread=None, agent=None)
    lowered = sql.strip().lower()
    assert lowered.startswith("select")
    for bad in ("insert", "update", "delete", "drop", "alter", "truncate"):
        assert bad not in lowered


def test_lanes_query_is_select_only():
    sql, params = db.build_lanes_query()
    lowered = sql.strip().lower()
    assert lowered.startswith("select")
    for bad in ("insert", "update", "delete", "drop", "alter", "truncate"):
        assert bad not in lowered


def test_lanes_query_selects_tmux_session():
    """Regression: the live-pane peek needs the per-INSTANCE self-registered
    session name (agent_status.tmux_session, migration 005), not just
    fleet_lanes.lane — a static label shared by a whole agent family (every
    cc-reviewer-N row has lane='reviewer') that can't resolve to one specific
    on-demand session when several are live at once (found live 2026-07-04)."""
    sql, params = db.build_lanes_query()
    assert "s.tmux_session" in sql


def test_messages_query_limit_clamped():
    # An absurd limit is clamped to MAX_LIMIT (DoS / oversized-read guard).
    sql, params = db.build_messages_query(limit=10_000, thread=None, agent=None)
    assert params[-1] <= db.MAX_LIMIT


def test_messages_query_filters_are_parameterized():
    sql, params = db.build_messages_query(limit=5, thread="t-1", agent="cc-cai")
    # Filters appear as bound params, not string-interpolated.
    assert "t-1" in params
    assert "cc-cai" in params
    assert "t-1" not in sql
    assert "cc-cai" not in sql


def test_pane_context_query_with_pct_selects_and_orders_by_pct():
    sql, params = db.build_pane_context_query(60, with_pct=True)
    assert ", pct," in sql
    assert "ORDER BY pct DESC NULLS LAST" in sql
    assert params == [60]


def test_pane_context_query_pct_less_degrades_gracefully():
    # op#13186 / cc-quality GATE-4 blocker 2: mig-043 absent must NOT dark the feed. The
    # pct-less form selects NULL::smallint AS pct (identical row shape -> downstream fails
    # closed to pane_k) and drops the pct sort key, so a missing column can't throw.
    sql, params = db.build_pane_context_query(60, with_pct=False)
    assert "NULL::smallint AS pct" in sql
    assert "pct DESC" not in sql
    assert "ORDER BY pane_k DESC NULLS LAST" in sql
    assert params == [60]


def test_pane_context_query_selects_pane_k_age_when_present():
    # never-blank (mig-057): when pane_k_at exists the query returns pane_k_age_s (seconds
    # since the hint was last seen) so the console can gate live/last-known/aged-out.
    sql, _ = db.build_pane_context_query(60, with_pane_k_at=True)
    assert "now() - pane_k_at" in sql and "AS pane_k_age_s" in sql


def test_pane_context_query_pane_k_age_degrades_when_absent():
    # mig-057 absent must NOT dark the feed: NULL::int AS pane_k_age_s keeps the row shape
    # (key present, None) so downstream treats every reading as LIVE = pre-057 behavior.
    sql, _ = db.build_pane_context_query(60, with_pane_k_at=False)
    assert "NULL::int AS pane_k_age_s" in sql
    assert "now() - pane_k_at" not in sql


# ---------------------------------------------------------------------------
# P1 asks-board (op#33598, console-revamp lane): the brief's coarse STATUS
# grouping (truly-outstanding / in-progress owned+dated / recently-answered),
# the closed-side answered query, and the TOPIC mapping (one tunable place).
# ---------------------------------------------------------------------------


def test_fleet_asks_query_selects_board_status_and_dates():
    sql, _ = db.build_fleet_asks_query()
    assert "AS board_status" in sql
    # in_progress = owned AND dated; every other open ask = truly_outstanding
    assert ("WHEN a.delegated_to IS NOT NULL AND a.committed_date IS NOT NULL "
            "THEN 'in_progress'") in sql
    assert "'truly_outstanding'" in sql
    # the board surfaces the dating reality, not just the derived status
    assert "a.committed_date" in sql
    assert "a.chase_by" in sql


def test_recently_answered_query_shape():
    sql, params = db.build_recently_answered_query()
    assert sql.lstrip().upper().startswith("SELECT")
    for bad in ("UPDATE", "DELETE", "INSERT"):
        assert bad not in sql.upper()
    assert "a.closed_at IS NOT NULL" in sql
    assert "a.triage_state = 'done'" in sql
    assert f"interval '{db.RECENTLY_ANSWERED_DAYS} days'" in sql
    assert "ORDER BY a.closed_at DESC" in sql
    assert "LIMIT" in sql
    # display text never falls back to the raw captured ask (op#61107 rule)
    assert "(awaiting triage)" in sql
    assert params == []


def test_topic_for_adcda_schedule_beats_cosem_owner():
    # adcda-schedule is a TOPIC distinct from cosem even when the work rides a
    # cosem-* lane: keyword evidence anywhere beats the cosem owner prefix.
    assert db.topic_for("cc-cosem-adcda-1", "Schedule editor: batch start rule") == "adcda-schedule"
    assert db.topic_for(None, "Fold ORBAT into Trainee Overview") == "adcda-schedule"
    assert db.topic_for("cc-cosem-platform-2", "ADCDA trainer list namelist IDs") == "adcda-schedule"


def test_topic_for_owner_prefixes():
    assert db.topic_for("cc-cosem-platform-2", "re-platform scope") == "cosem"
    assert db.topic_for("cc-irsyad-coord", "fee field on enrolment") == "irsyad"
    assert db.topic_for("cc-substrate-1", "logs consolidation") == "infra"
    assert db.topic_for("cc-fleet-health", "watchdog lease renewal") == "infra"
    # MUIS is the irsyad school's parent body — keyword beats the console owner
    assert db.topic_for("orch-console", "MUIS CEO email") == "irsyad"


def test_topic_for_other_when_nothing_matches():
    assert db.topic_for("cc-oeh", "Search Console ownership") == "other"
    assert db.topic_for(None, "") == "other"


def test_fetch_fleet_asks_annotates_topic(monkeypatch):
    monkeypatch.setattr(db, "_query", lambda sql, params: [
        {"id": 1, "text": "ADCDA batch start rule", "delegated_to": "cc-cosem-adcda-1"},
        {"id": 2, "text": "Tabung Jumaat PDF download", "delegated_to": "cc-irsyad-coord"},
        {"id": 3, "text": "unmatched thing", "delegated_to": "cc-oeh"},
    ])
    rows = db.fetch_fleet_asks()
    assert [r["topic"] for r in rows] == ["adcda-schedule", "irsyad", "other"]
