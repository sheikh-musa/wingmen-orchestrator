"""Memory 2.0 Part 2 PR C (op#36768, backlog #127): asks_open.py must REFUSE to
record a "waiting on Musa" ask when fleet_memory already records that thing as
done.

Why this is the point of the whole feature: a body that re-asks Musa something
he already answered spends the scarcest resource in the fleet — his attention —
and teaches him that the fleet does not remember. Part 2 gave every body the
facts at boot; this is the enforcement that stops a body asserting "waiting on
Musa" anyway.

Console's ruling (bus #87068): refuse on a match, with a per-fact escape hatch
(--accept-fact <id>) rather than a blanket override, so a refusal can only be
stepped over by naming the specific recorded fact the ask is knowingly
re-opening.

Runs entirely against tests/conftest.py's ephemeral pg_dsn. NEVER the live
substrate — this module inserts operator_asks rows.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from scripts.lib import fleet_memory

ao = importlib.import_module("scripts.asks_open")
from tests.migrations.test_139_fleet_memory_facts import (  # noqa: E402
    MIGRATION_135,
    MIGRATION_139,
    SILO,
    _ROLES,
    _drop_roles_created,
    _ensure_role,
    _make_ledger_table,
    _make_prereqs,
)

FACT = "DeepSeek console login done 30 Oct, profile in place"
ASK = "WAITING ON MUSA: DeepSeek console login still pending in the browser profile"
UNRELATED_ASK = "WAITING ON MUSA: pick the vault swap date"


@pytest.fixture
def ledger_with_facts(operator_ledger_db):
    """operator_asks (the real schema slice) + fleet_memory at migration 139.

    Depends on operator_ledger_db so DATABASE_URL points at the throwaway
    cluster for the duration of the test -- asks_open has no DSN seam for the
    fleet_memory lookup either, and it must never reach production.
    """
    dsn = f"{operator_ledger_db} application_name={SILO}"
    created = [r for r in _ROLES if _ensure_role(dsn, r)]
    try:
        _make_ledger_table(dsn)
        _make_prereqs(dsn)
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(MIGRATION_135.read_text())
            cur.execute(MIGRATION_139.read_text())
        yield dsn
    finally:
        _drop_roles_created(dsn, created)


def _insert_fact(dsn, fact=FACT, scope="fleet", scope_key=None, expires_at=None):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fleet_memory_insert(%s,%s,%s,%s,%s,NULL,%s,%s)",
            (scope, scope_key, fact, "op#36768", "cc-seed", None, expires_at),
        )
        return cur.fetchone()[0]


def _ask_rows(dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, ask FROM operator_asks ORDER BY id")
        return cur.fetchall()


# ── the refusal ──────────────────────────────────────────────────────────────

def test_an_ask_contradicting_a_recorded_fact_is_refused(ledger_with_facts):
    fact_id = _insert_fact(ledger_with_facts)
    with pytest.raises(fleet_memory.FactConflict):
        ao.open_ask(ASK, delegated_to="__test_body__")
    assert _ask_rows(ledger_with_facts) == [], "a refused ask must leave no row behind"


def test_the_refusal_names_the_fact_so_the_caller_can_check_it(ledger_with_facts):
    """The match is lexical, so it can be wrong. A body can only judge it if
    the refusal shows the exact row it matched."""
    fact_id = _insert_fact(ledger_with_facts)
    with pytest.raises(fleet_memory.FactConflict) as excinfo:
        ao.open_ask(ASK, delegated_to="__test_body__")
    assert f"#{fact_id}" in str(excinfo.value)
    assert "DeepSeek console login done" in str(excinfo.value)


def test_an_unrelated_ask_still_opens(ledger_with_facts):
    _insert_fact(ledger_with_facts)
    rid = ao.open_ask(UNRELATED_ASK, delegated_to="__test_body__")
    assert [r[1] for r in _ask_rows(ledger_with_facts)] == [UNRELATED_ASK]
    assert rid > 0


def test_a_retired_fact_does_not_refuse(ledger_with_facts):
    """A superseded fact is history, not a live claim: the thing can be pending
    again, and refusing would strand a genuinely open ask."""
    old = _insert_fact(ledger_with_facts)
    with psycopg.connect(ledger_with_facts, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fleet_memory_insert(%s,%s,%s,%s,%s,%s,NULL,NULL)",
            ("fleet", None, "DeepSeek console work handed to cc-finance on 31 Oct",
             "op#36768", "cc-seed", old),
        )
    assert ao.open_ask(ASK, delegated_to="__test_body__") > 0


def test_an_expired_fact_does_not_refuse(ledger_with_facts):
    """expires_at is the operator's own way of saying "believe this until
    THEN". An expired fact is no longer a fact, so it cannot refuse an ask."""
    _insert_fact(ledger_with_facts,
                 expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    assert ao.open_ask(ASK, delegated_to="__test_body__") > 0


# ── the escape hatch ─────────────────────────────────────────────────────────

def test_accept_fact_lets_the_ask_through_when_the_fact_is_named(ledger_with_facts):
    fact_id = _insert_fact(ledger_with_facts)
    rid = ao.open_ask(ASK, delegated_to="__test_body__", accept_fact=fact_id)
    assert [r[0] for r in _ask_rows(ledger_with_facts)] == [rid]


def test_accept_fact_pointing_at_an_unmatched_id_is_not_a_bypass(ledger_with_facts):
    """A typo'd or made-up id must not silently disable the check -- otherwise
    the escape hatch is a blanket override with extra steps."""
    _insert_fact(ledger_with_facts)
    with pytest.raises(ValueError) as excinfo:
        ao.open_ask(ASK, delegated_to="__test_body__", accept_fact=999999)
    assert "999999" in str(excinfo.value)
    assert _ask_rows(ledger_with_facts) == []


# ── deployment ordering: 139 is not live yet, and this must not break asks ───

def test_asks_still_open_when_fleet_memory_does_not_exist(operator_ledger_db, capsys):
    """139 is applied by console after this merges. Until then fleet_memory
    columns the helper selects (verified_at/expires_at) do not exist, and a
    refusal path that raises there would take out EVERY ask fleet-wide -- a far
    worse outage than the amnesia this check prevents.

    Since operator_ledger_db is the same database we are about to INSERT into,
    a genuinely dead DB still fails at the insert; only schema-drift degrades.
    """
    rid = ao.open_ask(ASK, delegated_to="__test_body__")
    assert rid > 0
    err = capsys.readouterr().err
    assert "fleet_memory check skipped" in err


# ── CLI surface ──────────────────────────────────────────────────────────────

def test_parser_accepts_accept_fact():
    args = ao.build_parser().parse_args(["the ask", "--accept-fact", "42"])
    assert args.accept_fact == 42


def test_parser_default_accept_fact_is_none():
    assert ao.build_parser().parse_args(["the ask"]).accept_fact is None


def test_cli_refusal_exits_nonzero_and_points_at_the_escape_hatch(
        ledger_with_facts, capsys):
    fact_id = _insert_fact(ledger_with_facts)
    rc = ao.main([ASK, "--delegated-to", "__test_body__"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "already records this as done" in err
    assert "--accept-fact" in err and str(fact_id) in err
    assert _ask_rows(ledger_with_facts) == []


def test_cli_accept_fact_opens_the_ask(ledger_with_facts, capsys):
    fact_id = _insert_fact(ledger_with_facts)
    rc = ao.main([ASK, "--delegated-to", "__test_body__", "--accept-fact", str(fact_id)])
    assert rc == 0
    assert str(_ask_rows(ledger_with_facts)[0][0]) in capsys.readouterr().out
