"""TDD proof for migrations/139_fleet_memory_facts.sql and
scripts/lib/fleet_memory.py — Memory 2.0 item 2A Part 2 (op#36768,
backlog #127; console plan approval bus #87062/#87068).

Migration 135 shipped fleet_memory without the fields the operator asked
for (verified_at, expires_at) and left a hole its own design notes did not
see: fleet_memory_immutable_trg pins an explicit column list, so ANY column
added later is silently writable by a bare UPDATE — the "one legal write
path" guarantee would quietly stop covering it. 139 adds the columns AND
extends that pinned list; both proofs live here.

scripts/lib/fleet_memory.py is the read/write helper every caller uses:
fail-closed identity on write, retired/expired filtering on read, and the
assert_not_pending() check that has to refuse a "waiting on Musa" ask when
a fact says he already did it.

Runs entirely against tests/conftest.py's ephemeral pg_dsn. NEVER touches
DATABASE_URL.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import apply_migration as am  # noqa: E402

from scripts.lib import fleet_memory  # noqa: E402

MIGRATION_135 = ROOT / "migrations" / "135_fleet_memory.sql"
MIGRATION_139 = ROOT / "migrations" / "139_fleet_memory_facts.sql"
SILO = "tscuymavysscrvoberrr"

_ROLES = ("anon", "authenticated", "service_role", "console_readonly")


def _ensure_role(dsn: str, rolename: str) -> bool:
    """Roles are cluster-wide in the shared session-scoped pg_dsn harness, so
    a second test creating the same role must not error (same helper shape as
    test_131_governance_floor.py's _ensure_role)."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (rolename,))
        if cur.fetchone():
            return False
        cur.execute(f'CREATE ROLE "{rolename}" NOLOGIN')
        return True


def _drop_roles_created(dsn: str, created: list[str]) -> None:
    if not created:
        return
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        for rolename in created:
            cur.execute(f'DROP ROLE IF EXISTS "{rolename}"')


def _make_ledger_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS migration_ledger (
                 repo text NOT NULL,
                 migration_name text NOT NULL,
                 silo_ref text NOT NULL,
                 sha256 text NOT NULL,
                 applied_at timestamptz NOT NULL DEFAULT now(),
                 applied_by text,
                 note text,
                 PRIMARY KEY (repo, migration_name, silo_ref)
               )"""
        )


def _make_prereqs(dsn: str) -> None:
    """Everything 135 assumes exists but does not itself define: the web roles,
    repo_context (its second change), and reject_audit_mutation() (defined by
    migration 131, stubbed here so this proof stays scoped to 135+139)."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE FUNCTION reject_audit_mutation() RETURNS trigger
               LANGUAGE plpgsql AS $$
               BEGIN
                   RAISE EXCEPTION 'audit table is append-only';
               END;
               $$"""
        )
        cur.execute(
            """CREATE TABLE repo_context (
                 repo text PRIMARY KEY
               )"""
        )


@pytest.fixture
def schema(fresh_db):
    # application_name carries the silo ref so apply_migration.py's residency
    # guard accepts the throwaway DSN (same trick test_131_governance_floor.py
    # uses).
    dsn = f"{fresh_db} application_name={SILO}"
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


def test_dry_run_via_apply_migration(fresh_db):
    """139's assert lines must parse and hold against a 135-era database."""
    dsn = f"{fresh_db} application_name={SILO}"
    created = [r for r in _ROLES if _ensure_role(dsn, r)]
    try:
        _make_ledger_table(dsn)
        _make_prereqs(dsn)
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(MIGRATION_135.read_text())
        result = am.apply_migration(dsn, MIGRATION_139, silo=SILO, dry_run=True)
        assert result["sha256"]
    finally:
        _drop_roles_created(dsn, created)


def _q(dsn, sql, params=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description:
            return cur.fetchall()
    return None


def _insert(dsn, scope, fact, op_ref, created_by="cc-test", scope_key=None,
            supersedes_id=None, expires_at=None):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fleet_memory_insert(%s, %s, %s, %s, %s, %s, %s, %s)",
            (scope, scope_key, fact, op_ref, created_by, supersedes_id,
             None, expires_at),
        )
        return cur.fetchone()[0]


# ── migration 139: the columns ─────────────────────────────────────────────

def test_verified_at_and_expires_at_exist(schema):
    cols = {r[0]: r for r in _q(
        schema,
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name='fleet_memory'",
    )}
    assert "verified_at" in cols
    assert cols["verified_at"][1] == "NO"
    assert "expires_at" in cols
    assert cols["expires_at"][1] == "YES"


def test_verified_at_defaults_to_insert_time(schema):
    mid = _insert(schema, "fleet", "a fact", "op#36768")
    row = _q(schema, "SELECT verified_at, created_at FROM fleet_memory WHERE id=%s",
             (mid,))[0]
    assert row[0] == row[1]


def test_six_positional_args_still_work(schema):
    """135's callers pass 6 args; the 8-arg replacement must stay callable
    the same way (the old 6-arg overload is dropped, not kept alongside)."""
    with psycopg.connect(schema) as conn, conn.cursor() as cur:
        cur.execute("SELECT fleet_memory_insert('fleet', NULL, 'six args', "
                    "'op#36768', 'cc-test', NULL)")
        assert cur.fetchone()[0] > 0
    sigs = _q(schema,
              "SELECT p.pronargs FROM pg_proc p "
              "WHERE p.proname='fleet_memory_insert'")
    assert sigs == [(8,)]


def test_insert_records_expires_at(schema):
    when = datetime.now(timezone.utc) + timedelta(days=30)
    mid = _insert(schema, "fleet", "time bound fact", "op#36768", expires_at=when)
    got = _q(schema, "SELECT expires_at FROM fleet_memory WHERE id=%s", (mid,))[0][0]
    assert abs((got - when).total_seconds()) < 1


# ── migration 139: the hole 135 left open ──────────────────────────────────

def test_bare_update_of_verified_at_raises(schema):
    mid = _insert(schema, "fleet", "pinned fact", "op#36768")
    with pytest.raises(psycopg.errors.RaiseException):
        _q(schema, "UPDATE fleet_memory SET verified_at = now() WHERE id=%s", (mid,))


def test_bare_update_of_expires_at_raises(schema):
    mid = _insert(schema, "fleet", "pinned fact", "op#36768")
    with pytest.raises(psycopg.errors.RaiseException):
        _q(schema, "UPDATE fleet_memory SET expires_at = now() + interval '1 day' "
                   "WHERE id=%s", (mid,))


def test_retire_via_supersede_still_allowed(schema):
    old = _insert(schema, "fleet", "fact v1", "op#36768")
    _insert(schema, "fleet", "fact v2", "op#36800", supersedes_id=old)
    retired_at, retired_op = _q(
        schema, "SELECT retired_at, retired_op_ref FROM fleet_memory WHERE id=%s",
        (old,))[0]
    assert retired_at is not None
    assert retired_op == "op#36800"


# ── scripts/lib/fleet_memory.py ────────────────────────────────────────────

def test_write_fact_refuses_without_identity(schema):
    with pytest.raises(Exception) as exc:
        fleet_memory.write_fact("fleet", "no identity", "op#36768",
                                dsn=schema, env={})
    assert "identi" in str(exc.value).lower()


def test_write_fact_resolves_identity_from_env(schema):
    mid = fleet_memory.write_fact("fleet", "env identity", "op#36768",
                                  dsn=schema,
                                  env={"CC_BASE_AGENT_ID": "cc-second-brain"})
    assert _q(schema, "SELECT created_by FROM fleet_memory WHERE id=%s",
              (mid,))[0][0] == "cc-second-brain"


def test_read_facts_excludes_retired(schema):
    old = _insert(schema, "fleet", "superseded fact", "op#36768")
    _insert(schema, "fleet", "current fact", "op#36800", supersedes_id=old)
    facts = fleet_memory.read_facts(dsn=schema)
    texts = [f.fact for f in facts]
    assert "current fact" in texts
    assert "superseded fact" not in texts


def test_read_facts_excludes_expired(schema):
    _insert(schema, "fleet", "stale fact", "op#36768",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    _insert(schema, "fleet", "live fact", "op#36768",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    texts = [f.fact for f in fleet_memory.read_facts(dsn=schema)]
    assert "live fact" in texts
    assert "stale fact" not in texts


def test_read_facts_includes_fleet_and_my_scopes_only(schema):
    _insert(schema, "fleet", "fleet-wide", "op#36768")
    _insert(schema, "repo", "repo-mine", "op#36768", scope_key="second-brain")
    _insert(schema, "repo", "repo-other", "op#36768", scope_key="cosem-adcda")
    _insert(schema, "client", "client-mine", "op#36768", scope_key="irsyad")
    _insert(schema, "client", "client-other", "op#36768", scope_key="someone")

    texts = [f.fact for f in fleet_memory.read_facts(
        repos=["second-brain"], clients=["irsyad"], dsn=schema)]
    assert "fleet-wide" in texts
    assert "repo-mine" in texts
    assert "client-mine" in texts
    assert "repo-other" not in texts
    assert "client-other" not in texts


def test_assert_not_pending_raises_on_matching_fact(schema):
    _insert(schema, "fleet",
            "DeepSeek console login done 30 Oct, profile ~/.wingmen/deepseek_browser",
            "op#36768")
    with pytest.raises(fleet_memory.FactConflict) as exc:
        fleet_memory.assert_not_pending(
            "DeepSeek console login still pending on Musa", dsn=schema)
    assert "op#36768" in str(exc.value)


def test_assert_not_pending_allows_unrelated_ask(schema):
    _insert(schema, "fleet",
            "DeepSeek console login done 30 Oct, profile ~/.wingmen/deepseek_browser",
            "op#36768")
    assert fleet_memory.assert_not_pending(
        "Musa to approve the Stripe keys", dsn=schema) is None


def test_assert_not_pending_ignores_generic_domain_overlap(schema):
    """cc-quality F1 on PR#743: two shared GENERIC fleet nouns are not a match.

    'console' and 'deploy' recur in every lane's prose; a two-token fact made
    only of them must not halt an unrelated ask about a different subject.
    A spurious refusal is the expensive error here -- it blocks a genuinely
    pending ask -- so the tripwire stays quiet unless the overlap is
    substantive relative to the ask, not just a raw count of 2.
    """
    _insert(schema, "fleet", "console deploy done", "op#36768")
    assert fleet_memory.assert_not_pending(
        "console deploy blocked on vault key", dsn=schema) is None


def test_assert_not_pending_catches_long_phrased_real_match(schema):
    """The F1 fix must not cost recall: the same fact still trips on a
    differently-worded ask that really is about it."""
    _insert(schema, "fleet",
            "DeepSeek console login done 30 Oct, profile ~/.wingmen/deepseek_browser",
            "op#36768")
    with pytest.raises(fleet_memory.FactConflict):
        fleet_memory.assert_not_pending(
            "Musa has not yet completed the DeepSeek login still pending",
            dsn=schema)


def test_assert_not_pending_ignores_expired_fact(schema):
    _insert(schema, "fleet", "DeepSeek console login done", "op#36768",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    assert fleet_memory.assert_not_pending(
        "DeepSeek console login still pending", dsn=schema) is None


def test_render_facts_block_is_bounded(schema):
    for i in range(40):
        _insert(schema, "fleet", f"fact number {i} " + ("x" * 60), "op#36768")
    block = fleet_memory.render_facts_block(fleet_memory.read_facts(dsn=schema))
    assert len(block) <= 1200
    assert "fact number 0" in block
