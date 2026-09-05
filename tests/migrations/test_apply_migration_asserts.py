"""Tests for scripts/apply_migration.py's `-- assert:` header (CAI-RESP-1397 #5).

Reproduces the exact defect cc-quality wet-proved, verified empirically
against a real ephemeral Postgres before writing these tests (not assumed):
every new function grants EXECUTE to PUBLIC by default, so `anon` (or any
role) is effectively executable via that PUBLIC grant with NO direct grant
of its own. `REVOKE EXECUTE ON FUNCTION f() FROM anon` in that state has
nothing to remove for `anon` specifically — Postgres silently no-ops it
(no error, no warning surfaced through psycopg by default) while the
function stays fully executable via PUBLIC. A migration author who forgets
`REVOKE ... FROM PUBLIC` (only remembering the named role) ships a REVOKE
that changes nothing — this is the realistic shape of the incident, not a
contrived one.

Runs entirely against the ephemeral PG17 harness in tests/migrations/conftest.py.
NEVER touches DATABASE_URL / any live silo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import apply_migration as am  # noqa: E402

SILO = "testsilo00000000000000"


def _make_ledger_table(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE migration_ledger (
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


@pytest.fixture
def ledger_db(fresh_db):
    dsn = f"{fresh_db} application_name={SILO}"
    _make_ledger_table(dsn)
    return dsn


def _write(tmp_path: Path, name: str, body: str, silo: str = SILO) -> Path:
    f = tmp_path / name
    f.write_text(f"-- ledger: silo={silo}\n{body}\n")
    return f


def _reset_anon_role(cur) -> None:
    # Roles are cluster-global, not schema-scoped — fresh_db only resets the
    # public schema, so a role created by an earlier test in this session
    # survives. Make every setup idempotent.
    cur.execute('DROP ROLE IF EXISTS anon')
    cur.execute('CREATE ROLE anon')


def _setup_fn_only_public_grant(dsn: str) -> None:
    """Creates public.widget_fn() with ONLY the default PUBLIC EXECUTE grant
    (never granted to `anon` directly) — verified empirically: REVOKE ...
    FROM anon in this state has nothing of anon's to remove and no-ops,
    while PUBLIC still makes it fully executable."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        _reset_anon_role(cur)
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")


def _setup_fn_with_direct_grant_no_public(dsn: str) -> None:
    """Creates public.widget_fn(), strips the default PUBLIC grant, and
    grants EXECUTE directly to `anon` — the ONLY path to the privilege is
    now the direct grant, so REVOKE ... FROM anon actually removes it."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        _reset_anon_role(cur)
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
        cur.execute('REVOKE EXECUTE ON FUNCTION widget_fn() FROM PUBLIC')
        cur.execute('GRANT EXECUTE ON FUNCTION widget_fn() TO anon')


def _has_execute(dsn: str) -> bool:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege('anon', 'public.widget_fn()', 'EXECUTE')")
        return cur.fetchone()[0]


# --------------------------------------------------------------------------------
# (a) a silently no-op'd REVOKE fails the migration with the assertion message
# --------------------------------------------------------------------------------

def test_silent_noop_revoke_fails_the_migration(ledger_db, tmp_path):
    _setup_fn_only_public_grant(ledger_db)
    f = _write(
        tmp_path, "001_revoke_widget.sql",
        "-- assert: no_execute anon public.widget_fn()\n"
        "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;",
    )
    with pytest.raises(am.Refuse, match="no_execute anon public.widget_fn\\(\\).*silent no-op"):
        am.apply_migration(ledger_db, f, silo=SILO)

    # rolled back: privilege is unaffected either way, but nothing else leaked
    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM migration_ledger")
        assert cur.fetchone()[0] == 0


def test_silent_noop_revoke_actually_left_privilege_intact(ledger_db, tmp_path):
    """Sanity check on the fixture itself: prove the no-op is real, not assumed."""
    _setup_fn_only_public_grant(ledger_db)
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon")
    assert _has_execute(ledger_db) is True


# --------------------------------------------------------------------------------
# (b) a real revoke (PUBLIC already stripped, direct grant removed) passes
# --------------------------------------------------------------------------------

def test_real_revoke_passes(ledger_db, tmp_path):
    _setup_fn_with_direct_grant_no_public(ledger_db)
    assert _has_execute(ledger_db) is True
    f = _write(
        tmp_path, "001_revoke_widget.sql",
        "-- assert: no_execute anon public.widget_fn()\n"
        "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;",
    )
    result = am.apply_migration(ledger_db, f, silo=SILO)
    assert result["status"] == "applied"
    assert result["assertions"] == [{"kind": "no_execute", "role": "anon", "fn": "public.widget_fn()", "passed": True}]
    assert _has_execute(ledger_db) is False


# --------------------------------------------------------------------------------
# (c) search_path / dropped assertions pass and fail correctly
# --------------------------------------------------------------------------------

def test_search_path_assert_passes_when_pinned(ledger_db, tmp_path):
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
    f = _write(
        tmp_path, "001_pin_search_path.sql",
        "-- assert: search_path public.widget_fn()\n"
        "ALTER FUNCTION widget_fn() SET search_path = pg_catalog, public;",
    )
    result = am.apply_migration(ledger_db, f, silo=SILO)
    assert result["status"] == "applied"
    assert result["assertions"][0]["passed"] is True


def test_search_path_assert_fails_when_not_pinned(ledger_db, tmp_path):
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
    f = _write(
        tmp_path, "001_pin_search_path.sql",
        "-- assert: search_path public.widget_fn()\n"
        # a migration that claims to pin search_path but forgets to (or targets the wrong fn)
        "SELECT 1;",
    )
    with pytest.raises(am.Refuse, match="search_path public.widget_fn\\(\\)"):
        am.apply_migration(ledger_db, f, silo=SILO)


def test_dropped_assert_passes_when_actually_dropped(ledger_db, tmp_path):
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
    f = _write(
        tmp_path, "001_drop_widget.sql",
        "-- assert: dropped public.widget_fn()\n"
        "DROP FUNCTION widget_fn();",
    )
    result = am.apply_migration(ledger_db, f, silo=SILO)
    assert result["status"] == "applied"
    assert result["assertions"][0]["passed"] is True


def test_dropped_assert_fails_when_still_present(ledger_db, tmp_path):
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
        cur.execute("CREATE FUNCTION other_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
    f = _write(
        tmp_path, "001_drop_wrong_fn.sql",
        "-- assert: dropped public.widget_fn()\n"
        # migration drops the WRONG function — widget_fn survives
        "DROP FUNCTION other_fn();",
    )
    with pytest.raises(am.Refuse, match="dropped public.widget_fn\\(\\) — function still exists"):
        am.apply_migration(ledger_db, f, silo=SILO)


def test_no_execute_assert_on_missing_function_fails(ledger_db, tmp_path):
    """A no_execute assert against a function that doesn't exist can't be
    verified as safe — fails closed rather than vacuously passing."""
    f = _write(
        tmp_path, "001_revoke_nonexistent.sql",
        "-- assert: no_execute anon public.does_not_exist()\n"
        "SELECT 1;",
    )
    with pytest.raises(am.Refuse, match="does not exist"):
        am.apply_migration(ledger_db, f, silo=SILO)


# --------------------------------------------------------------------------------
# (d) required-ness: a REVOKE/DROP FUNCTION migration with zero asserts refuses
# --------------------------------------------------------------------------------

def test_revoke_with_no_assertions_refuses_before_applying(ledger_db, tmp_path):
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        _reset_anon_role(cur)
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
        cur.execute("GRANT EXECUTE ON FUNCTION widget_fn() TO anon")
    f = _write(tmp_path, "001_bare_revoke.sql", "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;")
    with pytest.raises(am.Refuse, match="CAI-RESP-1397"):
        am.apply_migration(ledger_db, f, silo=SILO)
    # nothing ran — the privilege is untouched, proving refusal happened BEFORE apply
    assert _has_execute(ledger_db) is True


def test_drop_function_with_no_assertions_refuses_before_applying(ledger_db, tmp_path):
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
    f = _write(tmp_path, "001_bare_drop.sql", "DROP FUNCTION widget_fn();")
    with pytest.raises(am.Refuse, match="CAI-RESP-1397"):
        am.apply_migration(ledger_db, f, silo=SILO)
    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regprocedure('public.widget_fn()') IS NOT NULL")
        assert cur.fetchone()[0] is True


def test_revoke_with_assertion_present_does_not_trigger_required_refusal(ledger_db, tmp_path):
    _setup_fn_with_direct_grant_no_public(ledger_db)
    f = _write(
        tmp_path, "001_revoke_widget.sql",
        "-- assert: no_execute anon public.widget_fn()\n"
        "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;",
    )
    result = am.apply_migration(ledger_db, f, silo=SILO)
    assert result["status"] == "applied"


def test_non_revoke_migration_needs_no_assertions(ledger_db, tmp_path):
    f = _write(tmp_path, "001_make_table.sql", "create table widgets (id int);")
    result = am.apply_migration(ledger_db, f, silo=SILO)
    assert result["status"] == "applied"
    assert result["assertions"] == []


def test_unknown_assert_kind_refuses_before_applying(ledger_db, tmp_path):
    f = _write(
        tmp_path, "001_bad_assert.sql",
        "-- assert: no_such_kind anon public.widget_fn()\n"
        "create table widgets (id int);",
    )
    with pytest.raises(am.Refuse, match="unknown assert kind"):
        am.apply_migration(ledger_db, f, silo=SILO)
    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.widgets')")
        assert cur.fetchone()[0] is None  # never even ran


def test_malformed_no_execute_assert_refuses(ledger_db, tmp_path):
    f = _write(
        tmp_path, "001_bad_no_execute.sql",
        "-- assert: no_execute justonetoken\n"
        "create table widgets (id int);",
    )
    with pytest.raises(am.Refuse, match="malformed"):
        am.apply_migration(ledger_db, f, silo=SILO)


# --------------------------------------------------------------------------------
# (e) --dry-run runs assertions too and reports them, without committing
# --------------------------------------------------------------------------------

def test_dry_run_reports_passing_assertions_without_committing(ledger_db, tmp_path):
    _setup_fn_with_direct_grant_no_public(ledger_db)
    f = _write(
        tmp_path, "001_revoke_widget.sql",
        "-- assert: no_execute anon public.widget_fn()\n"
        "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;",
    )
    result = am.apply_migration(ledger_db, f, silo=SILO, dry_run=True)
    assert result["status"] == "dry_run_ok"
    assert result["assertions"][0]["passed"] is True

    # rolled back: privilege still present, nothing ledgered
    assert _has_execute(ledger_db) is True
    with psycopg.connect(ledger_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM migration_ledger")
        assert cur.fetchone()[0] == 0


def test_dry_run_surfaces_a_failing_assertion_and_still_rolls_back(ledger_db, tmp_path):
    _setup_fn_only_public_grant(ledger_db)
    f = _write(
        tmp_path, "001_revoke_widget.sql",
        "-- assert: no_execute anon public.widget_fn()\n"
        "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;",
    )
    with pytest.raises(am.Refuse, match="silent no-op"):
        am.apply_migration(ledger_db, f, silo=SILO, dry_run=True)
    assert _has_execute(ledger_db) is True


def test_main_cli_prints_assertion_lines(ledger_db, tmp_path, capsys):
    _setup_fn_with_direct_grant_no_public(ledger_db)
    f = _write(
        tmp_path, "001_revoke_widget.sql",
        "-- assert: no_execute anon public.widget_fn()\n"
        "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;",
    )
    rc = am.main([str(f), "--silo", SILO, "--dsn", ledger_db])
    assert rc == 0
    out = capsys.readouterr().out
    assert "assert no_execute anon public.widget_fn()" in out


def test_main_cli_refuse_on_required_assertion_missing(ledger_db, tmp_path, capsys):
    with psycopg.connect(ledger_db, autocommit=True) as conn, conn.cursor() as cur:
        _reset_anon_role(cur)
        cur.execute("CREATE FUNCTION widget_fn() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql")
    f = _write(tmp_path, "001_bare_revoke.sql", "REVOKE EXECUTE ON FUNCTION widget_fn() FROM anon;")
    rc = am.main([str(f), "--silo", SILO, "--dsn", ledger_db])
    assert rc == 3
    err = capsys.readouterr().err
    assert "CAI-RESP-1397" in err


# --------------------------------------------------------------------------------
# parse_assert_lines unit tests (no DB needed)
# --------------------------------------------------------------------------------

def test_parse_assert_lines_all_three_kinds():
    text = (
        "-- ledger: silo=x\n"
        "-- assert: no_execute anon public.fetch_and_execute_sql(text)\n"
        "-- assert: search_path public.get_decision(text)\n"
        "-- assert: dropped public.fetch_and_execute_sql(text)\n"
    )
    result = am.parse_assert_lines(text)
    assert result == [
        {"kind": "no_execute", "role": "anon", "fn": "public.fetch_and_execute_sql(text)"},
        {"kind": "search_path", "fn": "public.get_decision(text)"},
        {"kind": "dropped", "fn": "public.fetch_and_execute_sql(text)"},
    ]


def test_parse_assert_lines_empty_when_absent():
    assert am.parse_assert_lines("-- ledger: silo=x\ncreate table a (id int);") == []


def test_check_required_assertions_case_insensitive_revoke():
    with pytest.raises(am.Refuse, match="CAI-RESP-1397"):
        am.check_required_assertions("revoke execute on function f() from anon;", [])


def test_check_required_assertions_case_insensitive_drop_function():
    with pytest.raises(am.Refuse, match="CAI-RESP-1397"):
        am.check_required_assertions("Drop Function f();", [])


def test_check_required_assertions_ok_with_assertions_present():
    am.check_required_assertions("REVOKE ...", [{"kind": "dropped", "fn": "public.f()"}])  # must not raise
