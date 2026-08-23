"""Never-blank lane-context fix — Layer A (publish upsert) contract.

Exercises the SHIPPED upsert SQL (scripts.auto_recycle_on_bloat._PANE_UPSERT_SQL,
imported, not re-typed — [[wetprove-must-exercise-the-shipped-artifact]]) against a REAL
pane_context scratch row inside a transaction that is ALWAYS rolled back (zero live
residue). Locks Nazim bake-in (b): a mid-turn NULL pane_k capture must NOT overwrite a
good last-known pane_k, and must FREEZE (not bump) pane_k_at; a fresh non-null capture
updates BOTH.

Run (from ~/wingmen/orchestrator): .venv/bin/python -m pytest tests/test_pane_context_upsert.py -q
Needs DATABASE_URL (a real DB; the upsert is raw SQL). Skips if unset.
"""
import os
import pytest

psycopg = pytest.importorskip("psycopg")
from scripts.auto_recycle_on_bloat import _PANE_UPSERT_SQL  # the SHIPPED SQL

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
SCRATCH = "__never_blank_upsert_test__"


def _row(session, base, pane_k, pct, idle, host):
    return {"session": session, "base": base, "pane_k": pane_k,
            "pct": pct, "idle": idle, "host": host}


def _read(cur, session):
    cur.execute("SELECT pane_k, pct, idle_verdict, pane_k_at, updated_at "
                "FROM pane_context WHERE session=%s", (session,))
    return cur.fetchone()


@pytest.mark.skipif(not DSN, reason="DATABASE_URL not set")
def test_null_capture_keeps_last_known_and_freezes_pane_k_at():
    # now() is transaction-stable, so we seed pane_k_at to a fixed PAST value directly:
    # then FREEZE (stays at PAST) vs BUMP (moves to tx-now(), != PAST) are distinguishable
    # within the single rolled-back transaction.
    PAST = "2020-01-01 00:00:00+00"
    with psycopg.connect(DSN, autocommit=False) as conn, conn.cursor() as cur:
        try:
            # seed a row with a KNOWN-OLD pane_k_at (as if the hint was last seen long ago)
            cur.execute("DELETE FROM pane_context WHERE session=%s", (SCRATCH,))
            cur.execute("INSERT INTO pane_context (session, base, pane_k, pct, idle_verdict, host, "
                        "updated_at, pane_k_at) VALUES (%s,'cc-x',400.0,NULL,'WORKING','h', now(), %s)",
                        (SCRATCH, PAST))
            _, _, _, at0, _ = _read(cur, SCRATCH)

            # 1) MID-TURN capture: pane_k NULL (hint hidden), pct/idle fresh.
            #    pane_k KEPT (400), pane_k_at FROZEN at PAST, updated_at BUMPED.
            cur.executemany(_PANE_UPSERT_SQL, [_row(SCRATCH, "cc-x", None, 12, "STAGED", "h")])
            k1, pct1, idle1, at1, upd1 = _read(cur, SCRATCH)
            assert k1 == 400.0, "last-known pane_k must survive a NULL capture"
            assert at1 == at0, "pane_k_at must FREEZE on a NULL capture (age keeps growing)"
            assert pct1 == 12 and idle1 == "STAGED", "pct/idle_verdict stay fresh (pct never COALESCE-kept)"

            # 2) a fresh NON-NULL capture updates BOTH pane_k and pane_k_at (off PAST).
            cur.executemany(_PANE_UPSERT_SQL, [_row(SCRATCH, "cc-x", 550.0, None, "WORKING", "h")])
            k2, pct2, idle2, at2, upd2 = _read(cur, SCRATCH)
            assert k2 == 550.0, "a fresh reading overwrites pane_k"
            assert at2 > at0, "a fresh reading BUMPS pane_k_at off the frozen PAST value"
        finally:
            conn.rollback()  # zero live residue, ALWAYS
