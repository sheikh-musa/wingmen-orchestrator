"""Ephemeral-Postgres proof for scripts/work_item_reconcile.py and
scripts/work_item_equivalence_proof.py (substrate 2.0, ask 4494, bus #73176 items 4+5,
Musa op#34969).

Applies migrations/114_work_item_ssot.sql to the ephemeral PG17 harness (same
stand-in source tables as tests/migrations/test_114_work_item_ssot.py), then breaks
the mirror on purpose -- trigger disabled, source deleted, scope flipped, activity
back-dated -- and proves each divergence class is detected, repaired through the
migration's own work_item_mirror() path, and that the proof flips FAIL -> PASS.

NEVER touches DATABASE_URL: everything runs against tests/conftest.py's pg_dsn.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import work_item_reconcile as wir  # noqa: E402
import work_item_equivalence_proof as wiep  # noqa: E402

MIGRATION_PATH = ROOT / "migrations" / "114_work_item_ssot.sql"


def _make_source_tables(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE operator_asks (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                ask text NOT NULL,
                triage_state text NOT NULL DEFAULT 'captured'
                    CHECK (triage_state IN ('captured','ask','not_an_ask','done')),
                closed_at timestamptz, delegated_to text, committed_date timestamptz,
                triaged_by text)""")
        cur.execute("""
            CREATE TABLE agent_messages (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                from_agent text NOT NULL, to_agent text, subject text NOT NULL,
                requires_response boolean NOT NULL DEFAULT false,
                responded_at timestamptz, skipped_at timestamptz, claimed_by text,
                response_ref text, created_at timestamptz NOT NULL DEFAULT now())""")
        cur.execute("""
            CREATE TABLE held_commitments (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                owner_agent text NOT NULL, title text NOT NULL,
                status text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','fired','discharged','cancelled')),
                due_at timestamptz, blocked_on text, discharged_at timestamptz,
                discharged_by text, cancelled_at timestamptz, cancel_reason text)""")


@pytest.fixture
def db(fresh_db):
    _make_source_tables(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_PATH.read_text())
    return fresh_db


def _exec(dsn, sql, params=None):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _findings(dsn, **kw):
    with psycopg.connect(dsn) as conn:
        return wir.find_divergence(conn, **kw)


def _classes(findings):
    return sorted(f.cls for f in findings)


def _prove(dsn):
    with psycopg.connect(dsn) as conn:
        return wiep.prove(conn)


def _seed(dsn):
    """One live row per kind, mirrored by the triggers, plus one closed historical ask
    with NO work_item row (never backfilled, by design)."""
    _exec(dsn, "INSERT INTO operator_asks (ask, triage_state, delegated_to) VALUES ('open ask', 'ask', 'cc-x')")
    _exec(dsn, "INSERT INTO agent_messages (from_agent, subject, requires_response) VALUES ('a', 'need reply', true)")
    _exec(dsn, "INSERT INTO held_commitments (owner_agent, title, due_at) VALUES ('cc-y', 'ship it', now() + interval '1 day')")
    _exec(dsn, "ALTER TABLE operator_asks DISABLE TRIGGER work_item_mirror_ask_trg")
    _exec(dsn, "INSERT INTO operator_asks (ask, triage_state, closed_at) VALUES ('ancient closed', 'done', now() - interval '30 days')")
    _exec(dsn, "ALTER TABLE operator_asks ENABLE TRIGGER work_item_mirror_ask_trg")


# ── clean baseline ───────────────────────────────────────────────────────────────────
def test_clean_db_has_no_divergence_and_proof_passes(db):
    _seed(db)
    assert _findings(db) == []
    r = _prove(db)
    assert r["ok"], r
    assert {k: (e["open_source"], e["live_work_item"]) for k, e in r["kinds"].items()} == {
        "ask": (1, 1), "bus": (1, 1), "commitment": (1, 1)}


def test_closed_historical_row_without_mirror_is_not_a_finding(db):
    _seed(db)
    assert _exec(db, "SELECT count(*) FROM work_item WHERE source_table='operator_asks'") == [(1,)]
    assert _findings(db) == []


# ── MISSING ──────────────────────────────────────────────────────────────────────────
def test_missing_open_row_detected_and_repaired_via_mirror(db):
    _seed(db)
    _exec(db, "ALTER TABLE held_commitments DISABLE TRIGGER work_item_mirror_commitment_trg")
    (hc_id,), = _exec(db, "INSERT INTO held_commitments (owner_agent, title) VALUES ('cc-z', 'skipped by mirror') RETURNING id")
    _exec(db, "ALTER TABLE held_commitments ENABLE TRIGGER work_item_mirror_commitment_trg")

    f = _findings(db)
    assert [(x.cls, x.kind, x.source_id, x.derived_state, x.derived_owner) for x in f] == \
        [("MISSING", "commitment", hc_id, "owned", "cc-z")]
    assert not _prove(db)["ok"]

    with psycopg.connect(db) as conn:
        assert wir.repair(conn, f) == {"MISSING": 1}
    assert _findings(db) == []
    assert _prove(db)["ok"]
    assert _exec(db, "SELECT state, owner_agent FROM work_item WHERE source_table='held_commitments' AND source_id=%s", (hc_id,)) == [("owned", "cc-z")]
    assert _exec(db, "SELECT event, actor FROM work_item_audit wa JOIN work_item w ON w.id=wa.work_item_id WHERE w.source_id=%s AND w.source_table='held_commitments'", (hc_id,)) == [("mirrored", wir.ACTOR)]


# ── STALE ────────────────────────────────────────────────────────────────────────────
def test_stale_state_detected_and_repaired(db):
    _seed(db)
    _exec(db, "ALTER TABLE operator_asks DISABLE TRIGGER work_item_mirror_ask_trg")
    _exec(db, "UPDATE operator_asks SET delegated_to='cc-new' WHERE ask='open ask'")
    _exec(db, "ALTER TABLE operator_asks ENABLE TRIGGER work_item_mirror_ask_trg")

    f = _findings(db)
    assert [(x.cls, x.stored_owner, x.derived_owner) for x in f] == [("STALE", "cc-x", "cc-new")]
    with psycopg.connect(db) as conn:
        assert wir.repair(conn, f) == {"STALE": 1}
    assert _findings(db) == []
    assert _exec(db, "SELECT owner_agent FROM work_item WHERE source_table='operator_asks' AND state='owned'") == [("cc-new",)]


def test_stale_terminal_transition_detected(db):
    """Source closed while the mirror was off: work_item still says 'owned'."""
    _seed(db)
    _exec(db, "ALTER TABLE operator_asks DISABLE TRIGGER work_item_mirror_ask_trg")
    _exec(db, "UPDATE operator_asks SET triage_state='done', closed_at=now(), triaged_by='orch-console' WHERE ask='open ask'")
    _exec(db, "ALTER TABLE operator_asks ENABLE TRIGGER work_item_mirror_ask_trg")
    f = _findings(db)
    assert [(x.cls, x.stored_state, x.derived_state) for x in f] == [("STALE", "owned", "done")]
    r = _prove(db)
    assert not r["ok"]
    assert r["kinds"]["ask"]["live_without_open"] != []
    with psycopg.connect(db) as conn:
        wir.repair(conn, f)
    assert _prove(db)["ok"]
    assert _exec(db, "SELECT state, done_by FROM work_item WHERE source_table='operator_asks' AND state='done'") == [("done", "orch-console")]


# ── SOURCE_GONE ──────────────────────────────────────────────────────────────────────
def test_deleted_source_row_detected_and_dropped_attributably(db):
    _seed(db)
    _exec(db, "DELETE FROM held_commitments")
    f = _findings(db)
    assert [(x.cls, x.kind, x.stored_state) for x in f] == [("SOURCE_GONE", "commitment", "owned")]
    with psycopg.connect(db) as conn:
        assert wir.repair(conn, f) == {"SOURCE_GONE": 1}
    assert _findings(db) == []
    rows = _exec(db, "SELECT state, dropped_by, drop_reason FROM work_item WHERE source_table='held_commitments'")
    assert rows[0][0] == "dropped" and rows[0][1] == wir.ACTOR and "deleted" in rows[0][2]
    assert _exec(db, "SELECT count(*) FROM work_item_audit WHERE actor=%s AND event='state_changed'", (wir.ACTOR,)) == [(1,)]


# ── OUT_OF_SCOPE ─────────────────────────────────────────────────────────────────────
def test_bus_row_leaving_rr_scope_detected_and_dropped(db):
    _seed(db)
    _exec(db, "UPDATE agent_messages SET requires_response=false")  # trigger WHEN clause no longer fires
    f = _findings(db)
    assert [(x.cls, x.stored_state) for x in f] == [("OUT_OF_SCOPE", "captured")]
    assert not _prove(db)["ok"]
    with psycopg.connect(db) as conn:
        assert wir.repair(conn, f) == {"OUT_OF_SCOPE": 1}
    assert _findings(db) == []
    assert _prove(db)["ok"]
    assert _exec(db, "SELECT state, drop_reason FROM work_item WHERE source_table='agent_messages'")[0][0] == "dropped"


# ── ABANDONED (drop-invariant watchdog) ──────────────────────────────────────────────
def test_abandoned_row_pages_once_and_is_never_auto_repaired(db):
    _seed(db)
    _exec(db, "UPDATE held_commitments SET due_at = now() - interval '1 hour'")  # overdue, not parked
    _exec(db, "UPDATE work_item SET updated_at = now() - interval '100 hours' WHERE source_table='held_commitments'")
    now = datetime.now(timezone.utc)
    f = _findings(db, now=now, abandoned_floor_hours=72)
    assert _classes(f) == ["ABANDONED"]
    assert "no activity for 100h" in f[0].detail

    with psycopg.connect(db) as conn:
        assert wir.repair(conn, f) == {}          # never auto-repaired
    assert _classes(_findings(db, now=now)) == ["ABANDONED"]

    sent = []
    state = {}
    paged = wir.page_findings(f, now=now, page_state=state,
                              send_page=lambda s, b: sent.append((s, b)))
    assert len(paged) == 1 and len(sent) == 1
    assert sent[0][0] == "work_item_reconcile: 1 ABANDONED" and "held_commitments" in sent[0][1]
    # de-dupe inside the repage window; re-page after it
    assert wir.page_findings(f, now=now + timedelta(hours=1), page_state=state,
                             send_page=lambda s, b: sent.append((s, b))) == []
    assert len(sent) == 1
    assert len(wir.page_findings(f, now=now + timedelta(hours=25), page_state=state,
                                 send_page=lambda s, b: sent.append((s, b)))) == 1
    assert len(sent) == 2
    # a liveness page is not an equivalence failure
    assert _prove(db)["ok"]


def test_future_due_date_is_parked_not_abandoned(db):
    _seed(db)  # the seeded commitment is due tomorrow
    _exec(db, "UPDATE work_item SET updated_at = now() - interval '500 hours' WHERE source_table='held_commitments'")
    assert _findings(db) == []
    _exec(db, "UPDATE held_commitments SET due_at = now() - interval '1 hour'")  # now overdue; mirror bumps updated_at
    _exec(db, "UPDATE work_item SET updated_at = now() - interval '500 hours' WHERE source_table='held_commitments'")
    assert _classes(_findings(db)) == ["ABANDONED"]


def test_structural_divergence_pages_in_the_same_bus_row(db):
    _seed(db)
    _exec(db, "DELETE FROM held_commitments")
    _exec(db, "UPDATE work_item SET updated_at = now() - interval '100 hours' WHERE source_table='operator_asks'")
    f = _findings(db)
    assert _classes(f) == ["ABANDONED", "SOURCE_GONE"]
    sent = []
    wir.page_findings(f, now=datetime.now(timezone.utc), page_state={}, send_page=lambda s, b: sent.append((s, b)))
    assert sent[0][0] == "work_item_reconcile: 1 ABANDONED, 1 SOURCE_GONE"
    assert "STRUCTURAL" in sent[0][1] and "ABANDONED" in sent[0][1]


def test_captured_rows_are_never_abandoned(db):
    """captured = unowned; nobody is on the hook yet, so it is a triage backlog
    (priority_sla_watchdog's domain), not an abandoned mid-state row."""
    _seed(db)
    _exec(db, "UPDATE work_item SET updated_at = now() - interval '1000 hours' WHERE source_table='agent_messages'")
    assert _exec(db, "SELECT state FROM work_item WHERE source_table='agent_messages'") == [("captured",)]
    assert _findings(db) == []


# ── proof: legacy-definition deltas ──────────────────────────────────────────────────
def test_proof_explains_legacy_close_drift_and_skipped_rows(db):
    _seed(db)
    # not_an_ask with NULL closed_at: asks_reconcile_daily calls this CLOSE-drift; the
    # mirror already holds it as 'dropped'. The naive closed_at-IS-NULL count would say open.
    _exec(db, "INSERT INTO operator_asks (ask, triage_state, triaged_by) VALUES ('noise', 'not_an_ask', 'orch-console')")
    # RR + unresponded + skipped: reconcile_responded_at's raw filter includes it; dropped in wi.
    _exec(db, "INSERT INTO agent_messages (from_agent, subject, requires_response, skipped_at) VALUES ('a', 'old', true, now())")
    r = _prove(db)
    assert r["ok"], r
    assert r["kinds"]["ask"]["legacy_delta"]["count"] == 1
    assert r["kinds"]["ask"]["legacy_delta"]["unexplained"] == []
    assert r["kinds"]["bus"]["legacy_delta"]["count"] == 1
    assert r["kinds"]["bus"]["legacy_delta"]["unexplained"] == []
    assert r["kinds"]["ask"]["open_source"] == 1 and r["kinds"]["bus"]["open_source"] == 1
    text = wiep.render(r)
    assert text.startswith("# work_item equivalence proof -- PASS")


def test_proof_cli_writes_artifact_and_exit_code(db, tmp_path):
    _seed(db)
    out = tmp_path / "proof.md"
    assert wiep.main(["--dsn", db, "--out", str(out)]) == 0
    assert out.read_text().startswith("# work_item equivalence proof -- PASS")
    _exec(db, "DELETE FROM held_commitments")
    assert wiep.main(["--dsn", db, "--out", str(out)]) == 1
    assert "FAIL" in out.read_text() and "SOURCE_GONE" in out.read_text()


def test_reconcile_cli_dry_run_is_read_only_and_exit_codes(db, tmp_path):
    _seed(db)
    assert wir.main(["--dsn", db, "--page-state", str(tmp_path / "p.json")]) == 0
    _exec(db, "ALTER TABLE operator_asks DISABLE TRIGGER work_item_mirror_ask_trg")
    _exec(db, "INSERT INTO operator_asks (ask, triage_state) VALUES ('unmirrored', 'ask')")
    _exec(db, "ALTER TABLE operator_asks ENABLE TRIGGER work_item_mirror_ask_trg")
    assert wir.main(["--dsn", db, "--page-state", str(tmp_path / "p.json")]) == 2
    # dry-run wrote nothing
    assert _exec(db, "SELECT count(*) FROM work_item WHERE source_table='operator_asks'") == [(1,)]
    assert wir.main(["--dsn", db, "--repair", "--page-state", str(tmp_path / "p.json")]) == 2
    assert _exec(db, "SELECT count(*) FROM work_item WHERE source_table='operator_asks'") == [(2,)]
    assert wir.main(["--dsn", db, "--page-state", str(tmp_path / "p.json")]) == 0


def test_non_agent_actor_convention():
    assert not wir.is_agent_actor("source:operator_asks")
    assert not wir.is_agent_actor(wir.ACTOR)
    assert not wir.is_agent_actor("trigger:agent_messages")
    assert not wir.is_agent_actor(None)
    assert wir.is_agent_actor("cc-fleet-health")
