"""Wet-prove for scripts/ask_turnaround.py's refresh() (Musa op#34803).

Runs against the ephemeral PG17 harness (tests/conftest.py pg_dsn), building on
tests/migrations/test_121_operator_ask_turnaround.py's own ta_db fixture
pattern (migration 121's DDL, read from the file, plus the extra tables
refresh()'s SQL joins against: operator_messages, agent_messages).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
from scripts.ask_turnaround import refresh  # noqa: E402
from scripts.asks_daily_digest import fetch_turnaround_summary, fetch_delivered_but_unclosed  # noqa: E402

MIG = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations" / "121_operator_ask_turnaround.sql"


def _executable_sql(path: pathlib.Path) -> str:
    lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


@pytest.fixture
def ref_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role", "console_readonly"):
            cur.execute(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated")
        cur.execute("CREATE TABLE public.operator_asks (id bigint PRIMARY KEY, source_msg_id bigint, "
                    "ask text, closed_at timestamptz, triage_state text, triage_summary text)")
        cur.execute("CREATE TABLE public.operator_messages (id bigint PRIMARY KEY, created_at timestamptz NOT NULL)")
        cur.execute("CREATE TABLE public.agent_messages (id bigserial PRIMARY KEY, from_agent text NOT NULL, "
                    "subject text NOT NULL, created_at timestamptz NOT NULL)")
        cur.execute(_executable_sql(MIG))
    return fresh_db


def _ask(cur, ask_id, op, asked_at, delivered_at=None, closed_at=None, triage_state="ask",
         triage_summary=None):
    cur.execute("INSERT INTO operator_messages (id, created_at) VALUES (%s, %s)", (op, asked_at))
    cur.execute(
        "INSERT INTO operator_asks (id, source_msg_id, ask, closed_at, triage_state, triage_summary) "
        "VALUES (%s, %s, 'x', %s, %s, %s)",
        (ask_id, op, closed_at, triage_state, triage_summary))
    if delivered_at is not None:
        cur.execute("UPDATE operator_asks SET delivered_at = %s WHERE id = %s", (delivered_at, ask_id))


def _bus(cur, from_agent, subject, created_at):
    cur.execute("INSERT INTO agent_messages (from_agent, subject, created_at) VALUES (%s, %s, %s)",
                (from_agent, subject, created_at))


T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


def test_full_phase_split_matches_known_facemask_shape(ref_db):
    """Reproduces the exact shape #78194 validated by hand (ack, then a
    started-marker row, then delivered) -- queue_wait/queued_after_ack/
    build_to_live/total all line up."""
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = T0
        acked = T0 + timedelta(hours=3, minutes=58)
        started = acked + timedelta(minutes=36)
        delivered = started + timedelta(minutes=33)
        _ask(cur, 1, 101, asked, delivered_at=delivered)
        _bus(cur, "cc-cosem-adcda", "[cosem-adcda] ack op#101 queued", acked)
        _bus(cur, "cc-cosem-adcda", "[cosem-adcda] op#101 started building the fix", started)
        refresh(conn)
        cur.execute("SELECT surface, ack_agent, queue_wait, queued_after_ack, build_to_live, total "
                    "FROM operator_ask_turnaround WHERE ask_id = 1")
        surface, ack_agent, queue_wait, queued_after_ack, build_to_live, total = cur.fetchone()
        assert surface == "cosem-adcda"
        assert ack_agent == "cc-cosem-adcda"
        assert queue_wait == timedelta(hours=3, minutes=58)
        assert queued_after_ack == timedelta(minutes=36)
        assert build_to_live == timedelta(minutes=33)
        assert total == timedelta(hours=4, minutes=67)


def test_unmatched_ask_gets_null_phases_but_a_real_total(ref_db):
    """No lane ever cites op#N in a subject -- counted (surface='unmatched'),
    not dropped, phases NULL, total still asked->delivered (#78194)."""
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = T0
        delivered = T0 + timedelta(seconds=46)
        _ask(cur, 2, 102, asked, delivered_at=delivered)
        refresh(conn)
        cur.execute("SELECT surface, ack_agent, queue_wait, queued_after_ack, build_to_live, total "
                    "FROM operator_ask_turnaround WHERE ask_id = 2")
        surface, ack_agent, queue_wait, queued_after_ack, build_to_live, total = cur.fetchone()
        assert surface == "unmatched"
        assert ack_agent is None
        assert queue_wait is None
        assert queued_after_ack is None
        assert build_to_live is None
        assert total == timedelta(seconds=46)


def test_body_mention_is_not_an_ack_subject_only(ref_db):
    """#78194 found a false ack from a BODY mention -- the join is subject
    only, so a row that only names op#N in its (non-existent here) body
    must not count as an ack. This table has no body column at all, so
    this also guards against ever adding one without re-checking the join."""
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = T0
        acked = T0 + timedelta(hours=1)
        delivered = T0 + timedelta(hours=2)
        _ask(cur, 3, 103, asked, delivered_at=delivered)
        _bus(cur, "cc-cosem-platform", "unrelated subject, no op# cited", acked)
        refresh(conn)
        cur.execute("SELECT surface FROM operator_ask_turnaround WHERE ask_id = 3")
        assert cur.fetchone()[0] == "unmatched"


def test_orch_console_and_fleet_health_rows_never_count_as_the_ack(ref_db):
    """The asker (orch-console) and the triager (cc-fleet-health, incl. its
    own numbered instances) are excluded from ack detection -- a genuine
    lane ack must come from someone else (#78194)."""
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = T0
        delivered = T0 + timedelta(hours=1)
        _ask(cur, 4, 104, asked, delivered_at=delivered)
        _bus(cur, "orch-console", "re op#104 noted", asked + timedelta(minutes=5))
        _bus(cur, "cc-fleet-health", "op#104 triaged", asked + timedelta(minutes=10))
        _bus(cur, "cc-fleet-health-1", "op#104 triaged by instance", asked + timedelta(minutes=15))
        refresh(conn)
        cur.execute("SELECT surface, ack_agent FROM operator_ask_turnaround WHERE ask_id = 4")
        assert cur.fetchone() == ("unmatched", None)


def test_surface_mapping_exact_families_else_fleet(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        for ask_id, op, agent, expected in (
            (5, 105, "cc-cosem-adcda-2", "cosem-adcda"),
            (6, 106, "cc-cosem-platform-3", "cosem-platform"),
            (7, 107, "cc-irsyad-coord", "fleet"),
        ):
            asked = T0
            delivered = T0 + timedelta(hours=1)
            _ask(cur, ask_id, op, asked, delivered_at=delivered)
            _bus(cur, agent, f"op#{op} ack", asked + timedelta(minutes=5))
        refresh(conn)
        cur.execute("SELECT ask_id, surface FROM operator_ask_turnaround WHERE ask_id IN (5,6,7) ORDER BY ask_id")
        assert cur.fetchall() == [(5, "cosem-adcda"), (6, "cosem-platform"), (7, "fleet")]


def test_delivered_at_preferred_over_closed_at_but_closed_at_is_the_fallback(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = T0
        closed = T0 + timedelta(hours=5)
        _ask(cur, 8, 108, asked, delivered_at=None, closed_at=closed)
        refresh(conn)
        cur.execute("SELECT delivered_at, delivered_source, total FROM operator_ask_turnaround WHERE ask_id = 8")
        delivered_at, source, total = cur.fetchone()
        assert source == "closed_at"
        assert total == timedelta(hours=5)


def test_neither_delivered_nor_closed_is_not_eligible(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        _ask(cur, 9, 109, T0, delivered_at=None, closed_at=None)
        n = refresh(conn)
        cur.execute("SELECT count(*) FROM operator_ask_turnaround WHERE ask_id = 9")
        assert cur.fetchone()[0] == 0
        assert n == 0


def test_refresh_is_idempotent_rerun_overwrites_not_duplicates(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        _ask(cur, 10, 110, T0, delivered_at=T0 + timedelta(hours=1))
        refresh(conn)
        refresh(conn)
        cur.execute("SELECT count(*) FROM operator_ask_turnaround WHERE ask_id = 10")
        assert cur.fetchone()[0] == 1


def test_refresh_returns_eligible_count(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        _ask(cur, 11, 111, T0, delivered_at=T0 + timedelta(hours=1))
        _ask(cur, 12, 112, T0, delivered_at=T0 + timedelta(hours=1))
        _ask(cur, 13, 113, T0, delivered_at=None, closed_at=None)
        n = refresh(conn)
        assert n == 2


# ── fetch_turnaround_summary / fetch_delivered_but_unclosed (digest wiring) ──
def test_fetch_turnaround_summary_excludes_unmatched_from_headline(ref_db):
    """A day with one matched ask and one unmatched (no lane ack) ask:
    delivered/median/p90/slowest must reflect ONLY the matched one."""
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        day = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)
        asked = day - timedelta(hours=1)
        _ask(cur, 1, 201, asked, delivered_at=day, triage_summary="matched one")
        _bus(cur, "cc-cosem-adcda", "op#201 ack", asked + timedelta(minutes=5))
        _ask(cur, 2, 202, day - timedelta(seconds=46), delivered_at=day, triage_summary="instant reply")
        refresh(conn)
        summary = fetch_turnaround_summary(conn, day=day.date())
        assert summary["delivered"] == 1
        assert summary["unmatched"] == 1
        assert summary["slowest"][0] == "matched one"


def test_fetch_turnaround_summary_none_when_nothing_matched_that_day(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        day = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)
        _ask(cur, 1, 201, day - timedelta(seconds=10), delivered_at=day)  # unmatched only
        refresh(conn)
        assert fetch_turnaround_summary(conn, day=day.date()) is None


def test_fetch_turnaround_summary_defaults_to_yesterday_singapore(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        from zoneinfo import ZoneInfo
        yesterday_sgt = (datetime.now(ZoneInfo("Asia/Singapore")) - timedelta(days=1))
        day_start = yesterday_sgt.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        _ask(cur, 1, 201, day_start - timedelta(hours=1), delivered_at=day_start, triage_summary="x")
        _bus(cur, "cc-cosem-adcda", "op#201 ack", day_start - timedelta(minutes=55))
        refresh(conn)
        summary = fetch_turnaround_summary(conn)  # no explicit day -> defaults to yesterday SGT
        assert summary is not None
        assert summary["delivered"] == 1


def test_fetch_delivered_but_unclosed_finds_the_hygiene_gap(ref_db):
    """#78194's exact data gap #1: a lane subject says 'done' but the ask
    itself is still open (closed_at AND delivered_at both NULL)."""
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)
        _ask(cur, 1, 201, asked, delivered_at=None, closed_at=None, triage_state="ask")
        _bus(cur, "cc-cosem-adcda", "op#201 done, deployed", asked + timedelta(minutes=10))
        result = fetch_delivered_but_unclosed(conn)
        assert len(result) == 1
        assert result[0][0] == 1  # ask_id
        assert result[0][1] == 201  # op


def test_fetch_delivered_but_unclosed_excludes_already_closed_asks(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)
        _ask(cur, 1, 201, asked, delivered_at=asked + timedelta(hours=1), triage_state="ask")
        _bus(cur, "cc-cosem-adcda", "op#201 done, deployed", asked + timedelta(minutes=10))
        result = fetch_delivered_but_unclosed(conn)
        assert result == []


def test_fetch_delivered_but_unclosed_excludes_console_and_fleet_health_self_claims(ref_db):
    with psycopg.connect(ref_db, autocommit=True) as conn, conn.cursor() as cur:
        asked = datetime(2026, 10, 29, 6, tzinfo=timezone.utc)
        _ask(cur, 1, 201, asked, delivered_at=None, closed_at=None, triage_state="ask")
        _bus(cur, "cc-fleet-health", "op#201 done", asked + timedelta(minutes=5))
        _bus(cur, "orch-console", "op#201 done", asked + timedelta(minutes=6))
        result = fetch_delivered_but_unclosed(conn)
        assert result == []
