"""The operator_asks test fixture must carry EVERY live CHECK and trigger (orch-console #79986).

PR#584's first version passed 339 tests and would still have failed live: the hand-written
fixture lacked mig127's operator_asks_bus_evidence_backfill_only_chk. The fixture now applies
the committed live snapshot (tests/fixtures/schema/operator_asks.live.json, written by
scripts/snapshot_live_checks.py --write). These tests pin fixture == snapshot. Snapshot ==
live is the read-only `snapshot_live_checks.py --check`, which never runs from pytest: tests
must never touch the live substrate.
"""
import re

import psycopg

from tests.conftest import NOT_MIRRORED_TRIGGERS, live_checks_snapshot

SNAP = live_checks_snapshot("operator_asks")


def _norm(sql):
    return re.sub(r"\s+", " ", sql).strip()


def test_fixture_has_exactly_the_live_check_constraints(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'operator_asks'::regclass AND contype = 'c'")
        fixture = {n: _norm(d) for n, d in cur.fetchall()}
    assert fixture == {n: _norm(d) for n, d in SNAP["checks"].items()}


def test_fixture_has_every_live_trigger_except_the_justified_exclusions(operator_ledger_db):
    with psycopg.connect(operator_ledger_db) as c, c.cursor() as cur:
        cur.execute("SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger "
                    "WHERE tgrelid = 'operator_asks'::regclass AND NOT tgisinternal")
        fixture = {n: _norm(d) for n, d in cur.fetchall()}
    expected = {n: _norm(t["def"]) for n, t in SNAP["triggers"].items() if n not in NOT_MIRRORED_TRIGGERS}
    assert fixture == expected


def test_each_excluded_trigger_is_still_non_blocking_in_the_live_snapshot():
    # if a refreshed snapshot ever shows the mirror trigger able to raise, the exclusion is
    # no longer safe and this fails until the fixture mirrors it
    for name in NOT_MIRRORED_TRIGGERS:
        t = SNAP["triggers"][name]
        assert t["def"].startswith(f"CREATE TRIGGER {name} AFTER "), t["def"]
        assert "EXCEPTION WHEN OTHERS THEN" in t["function_def"]
        assert "RAISE WARNING" in t["function_def"]


def test_the_check_that_bit_pr584_is_in_the_snapshot():
    assert "operator_asks_bus_evidence_backfill_only_chk" in SNAP["checks"]
