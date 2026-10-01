"""scripts/reclassify_client_asks.py — bus #47184 item 3, re-run
classify_client_ask() over already-captured client-channel rows. Round-trip
against the ephemeral operator_ledger_db fixture (tests/conftest.py) — never
production."""
import importlib
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

rc_mod = importlib.import_module("scripts.reclassify_client_asks")


def _insert_ask(conn, ask="please add feature X", delegated_to="cc-irsyad-coord",
                 triage_state="captured", triaged_by=None, closed_at=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_asks "
            "  (ask, ask_surface, delegated_to, chase_by, triage_state, triaged_by, closed_at) "
            "VALUES (%s,'client-channel',%s, now() + interval '24 hours', %s, %s, %s) "
            "RETURNING id",
            (ask, delegated_to, triage_state, triaged_by, closed_at),
        )
        rid = cur.fetchone()[0]
    conn.commit()
    return rid


def test_fetch_open_rows_includes_heuristic_and_untriaged(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        id1 = _insert_ask(c, triaged_by=None)
        id2 = _insert_ask(c, triaged_by="heuristic")
    with psycopg.connect(operator_ledger_db) as c:
        rows = rc_mod.fetch_open_rows(c)
    assert {r["id"] for r in rows} == {id1, id2}


def test_fetch_open_rows_excludes_closed_rows(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        _insert_ask(c, triaged_by="heuristic", closed_at=datetime.now(timezone.utc))
    with psycopg.connect(operator_ledger_db) as c:
        rows = rc_mod.fetch_open_rows(c)
    assert rows == []


# ── reclassifiable: pure, no DB ────────────────────────────────────────────────
def test_reclassifiable_includes_heuristic_and_null():
    rows = [{"id": 1, "triaged_by": None}, {"id": 2, "triaged_by": "heuristic"}]
    assert {r["id"] for r in rc_mod.reclassifiable(rows)} == {1, 2}


def test_reclassifiable_excludes_human_triaged_rows():
    rows = [{"id": 1, "triaged_by": "orch-console"}]
    assert rc_mod.reclassifiable(rows) == []


# ── compute_changes: pure, no DB ──────────────────────────────────────────────
def test_compute_changes_flags_chatter_wrongly_marked_ask():
    rows = [{"id": 1, "ask": "Lolol", "delegated_to": "cc-cosem-platform", "triage_state": "ask"}]
    changes = rc_mod.compute_changes(rows)
    assert changes == [(1, "cc-cosem-platform", "ask", "not_an_ask")]


def test_compute_changes_flags_real_ask_wrongly_marked_captured():
    rows = [{"id": 2, "ask": "It's more than a day. It needs to be done",
             "delegated_to": "cc-irsyad-coord", "triage_state": "captured"}]
    changes = rc_mod.compute_changes(rows)
    assert changes == [(2, "cc-irsyad-coord", "captured", "ask")]


def test_compute_changes_skips_rows_whose_classification_is_unchanged():
    rows = [{"id": 3, "ask": "please add feature X",
             "delegated_to": "cc-irsyad-coord", "triage_state": "ask"}]
    assert rc_mod.compute_changes(rows) == []


def test_apply_changes_updates_state_and_stamps_heuristic(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_ask(c, ask="Lolol", triage_state="ask", triaged_by="heuristic")
    with psycopg.connect(operator_ledger_db) as c:
        rc_mod.apply_changes(c, [(rid, "cc-irsyad-coord", "ask", "not_an_ask")])
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_state, triaged_by FROM operator_asks WHERE id=%s", (rid,))
        state, triaged_by = cur.fetchone()
    assert state == "not_an_ask"
    assert triaged_by == "heuristic"


def test_apply_changes_never_touches_rows_not_in_changes_list(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_ask(c, triage_state="ask", triaged_by="heuristic")
    with psycopg.connect(operator_ledger_db) as c:
        rc_mod.apply_changes(c, [])
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_state FROM operator_asks WHERE id=%s", (rid,))
        (state,) = cur.fetchone()
    assert state == "ask"


# ── project_post_state / summarize_by_lane: pure, no DB ───────────────────────
def test_project_post_state_overlays_changes_only():
    rows = [
        {"id": 1, "delegated_to": "l1", "triage_state": "ask", "chase_by": None},
        {"id": 2, "delegated_to": "l1", "triage_state": "captured", "chase_by": None},
    ]
    changes = [(1, "l1", "ask", "not_an_ask")]
    projected = rc_mod.project_post_state(rows, changes)
    by_id = {r["id"]: r["triage_state"] for r in projected}
    assert by_id == {1: "not_an_ask", 2: "captured"}


def test_summarize_by_lane_counts_asks_and_overdue():
    now = datetime.now(timezone.utc)
    rows = [
        {"id": 1, "delegated_to": "l1", "triage_state": "ask", "chase_by": now - timedelta(hours=1)},
        {"id": 2, "delegated_to": "l1", "triage_state": "ask", "chase_by": now + timedelta(hours=1)},
        {"id": 3, "delegated_to": "l1", "triage_state": "not_an_ask", "chase_by": now - timedelta(hours=1)},
        {"id": 4, "delegated_to": "l2", "triage_state": "ask", "chase_by": now - timedelta(hours=1)},
    ]
    summary = rc_mod.summarize_by_lane(rows, now=now)
    by_lane = {r["delegated_to"]: r for r in summary}
    assert by_lane["l1"] == {"delegated_to": "l1", "asks": 2, "overdue_asks": 1, "total": 3}
    assert by_lane["l2"] == {"delegated_to": "l2", "asks": 1, "overdue_asks": 1, "total": 1}


def test_render_summary_is_no_pii():
    changes = [(1, "cc-irsyad-coord", "ask", "not_an_ask")]
    lane_summary = [{"delegated_to": "cc-irsyad-coord", "asks": 0, "overdue_asks": 0, "total": 1}]
    out = rc_mod.render_summary(changes, lane_summary)
    assert "cc-irsyad-coord" in out
    assert "ask -> not_an_ask: 1" in out


def test_main_dry_run_writes_nothing_and_projects_post_state(operator_ledger_db, capsys):
    with psycopg.connect(operator_ledger_db) as c:
        _insert_ask(c, ask="Lolol", triage_state="ask", triaged_by="heuristic")
    rc = rc_mod.main(["--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 would change" in out
    assert "0 ask (0 overdue) / 1 total open" in out, "dry-run must project the post-change state, not re-query the untouched DB"
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_state FROM operator_asks")
        (state,) = cur.fetchone()
    assert state == "ask", "dry-run must not write"


def test_main_real_run_applies_and_reports(operator_ledger_db, capsys):
    with psycopg.connect(operator_ledger_db) as c:
        rid = _insert_ask(c, ask="Lolol", triage_state="ask", triaged_by="heuristic")
    rc = rc_mod.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 would change" in out
    assert "Lolol" not in out, "no-PII: raw ask text must never print in the report"
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT triage_state FROM operator_asks WHERE id=%s", (rid,))
        (state,) = cur.fetchone()
    assert state == "not_an_ask"
