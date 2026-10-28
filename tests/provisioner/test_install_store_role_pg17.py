"""PG17 harness half of install_ceayj_provisioner step 3 — the grant set is
EXACTLY the bundles' privilege surface, proven by executing REAL bundles as
fleet_provisioner on ephemeral PostgreSQL 17 (ceayj itself runs 17.6).

The installer runs the same render_store_grants() SQL as the admin side and
then proves the surface with the same transactional canary; this file is the
CI-grade proof: superuser applies the grants once, then EVERYTHING the
provisioner will ever do on the store happens AS the dedicated role — full
forward bundle, full revert bundle — plus the negative pins (no org-wide
SELECT, no superuser escalation). Skips cleanly when no PG17 binary is
available (pg_dsn fixture).
"""
import importlib.util
import sys
from pathlib import Path

import psycopg
import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner.render import render_bundle  # noqa: E402
from provisioner.spec import load_spec, spec_path  # noqa: E402

_mod_path = ORCH_DIR / "scripts" / "install_ceayj_provisioner.py"
_ispec = importlib.util.spec_from_file_location("install_ceayj_provisioner", _mod_path)
inst = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(inst)

# mirrors what Supabase ships and the live ceayj store carries (same prelude
# as test_pg17_integration.py — the bundle's REVOKEs name these roles)
_HARNESS_PRELUDE = """
DO $$ BEGIN CREATE ROLE anon NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE TABLE IF NOT EXISTS organizations (
  id       UUID PRIMARY KEY,
  name     TEXT NOT NULL,
  uen      TEXT,
  type     TEXT NOT NULL DEFAULT 'business',
  slug     TEXT,
  settings JSONB NOT NULL DEFAULT '{}'
);
"""


@pytest.fixture
def store(pg_dsn):
    from tests.conftest import assert_dsn_is_not_production

    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(_HARNESS_PRELUDE)
    yield pg_dsn
    # teardown to a bare store (session-scoped cluster — nothing may raise)
    spec, bundle = _spec_bundle()
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        try:
            conn.execute(bundle.revert_sql)
        except Exception:
            pass
        conn.execute(f"DROP TABLE IF EXISTS public.{spec.history_table}")
        conn.execute(f"DROP TABLE IF EXISTS public.{spec.content_table}")
        conn.execute("DROP TABLE IF EXISTS public.migration_ledger")  # bundle-created, owned by fp
        conn.execute("DROP TABLE IF EXISTS public.__fp_install_canary")
        try:
            conn.execute(f"REVOKE ALL ON SCHEMA public FROM {spec.editor_role}")
        except Exception:
            pass
        conn.execute(f"DROP ROLE IF EXISTS {spec.editor_role}")
        conn.execute("DROP ROLE IF EXISTS __fp_install_canary")
        conn.execute("DELETE FROM organizations WHERE id = %s", (spec.org_uuid,))
        conn.execute("REVOKE ALL ON SCHEMA public FROM fleet_provisioner")
        conn.execute("REVOKE INSERT, DELETE, REFERENCES ON TABLE public.organizations FROM fleet_provisioner")
        conn.execute("REVOKE SELECT (id) ON TABLE public.organizations FROM fleet_provisioner")
        conn.execute("DROP ROLE IF EXISTS fleet_provisioner")


def _spec_bundle():
    spec = load_spec(spec_path("bariq"))
    return spec, render_bundle(spec)


def _fp(store):
    return store.replace("user=postgres", "user=fleet_provisioner")


def test_store_grants_are_idempotent(store):
    with psycopg.connect(store, autocommit=True) as conn:
        conn.execute(inst.render_store_grants())
        conn.execute(inst.render_store_grants())  # step-3 re-run is a no-op


def test_role_password_mint_executes_on_real_postgres_through_psycopg3(store):
    """bus #80224: psycopg3 binds parameters SERVER-SIDE, and utility
    statements (ALTER ROLE ...) reject bind params outright -- the step-2/3
    "ALTER ROLE ... PASSWORD %s" shape raised psycopg SyntaxError on the
    live apply. The mint must build the statement client-side via
    sql.Literal instead. Proven against the real PG17 harness, not a mock,
    for BOTH roles the installer mints, and the old shape is pinned to the
    exact failure class so this test can never pass vacuously."""
    import secrets as _secrets

    pw = "TestPw_" + _secrets.token_urlsafe(18)
    roles = ["provisioner_log_role", "fleet_provisioner"]
    try:
        with psycopg.connect(store, autocommit=True) as conn:
            for role in roles:
                conn.execute(
                    f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; "
                    f"EXCEPTION WHEN duplicate_object THEN NULL; END $$")
                # the OLD shape must fail on real postgres through psycopg3
                with pytest.raises(psycopg.errors.SyntaxError) as excinfo:
                    conn.execute(f"ALTER ROLE {role} LOGIN PASSWORD %s", (pw,))
                assert pw not in str(excinfo.value), "password leaked in the error text"
                # the installer's mint path must succeed and actually set LOGIN
                inst._set_role_password(conn.cursor(), role, pw)
                can_login = conn.execute(
                    "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                    (role,)).fetchone()[0]
                assert can_login is True, f"{role} did not get LOGIN via _set_role_password"
    finally:
        # fleet_provisioner is owned by the store fixture's teardown (its
        # unconditional REVOKEs assume it exists) -- only the log role is
        # this test's exclusive residue
        with psycopg.connect(store, autocommit=True) as conn:
            conn.execute("DROP ROLE IF EXISTS provisioner_log_role")


def test_grant_set_is_exactly_the_bundles_surface_on_pg17(store):
    spec, bundle = _spec_bundle()
    # the admin side: exactly the installer's grant SQL, then a LOGIN the
    # harness can connect as (trust auth — the password mint is an
    # apply-time secret, not a harness concern)
    with psycopg.connect(store, autocommit=True) as conn:
        conn.execute(inst.render_store_grants())
        conn.execute("ALTER ROLE fleet_provisioner LOGIN")
    fp = _fp(store)

    # 1. the transactional install canary, AS the role: full surface, no residue
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute(inst.render_install_canary())
            cur.execute("SELECT to_regclass('public.__fp_install_canary')")
            assert cur.fetchone()[0] is None, "canary table visible after ROLLBACK"
        conn.rollback()
    with psycopg.connect(store, autocommit=True) as conn:
        n = conn.execute("SELECT count(*) FROM organizations WHERE id = '00000000-0000-0000-0000-00000000c0de'").fetchone()[0]
        assert n == 0, "canary org row survived the ROLLBACK"
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = '__fp_install_canary'").fetchone()
        assert exists is None, "canary role survived the ROLLBACK"

    # 2. negative pins: exactly-enough means denied where the bundles never go
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM organizations LIMIT 1")  # SELECT(id) is granted
            cur.fetchall()
            for probe in ("SELECT name FROM organizations LIMIT 1",   # tenant data — denied
                          "GRANT postgres TO fleet_provisioner"):     # escalation — denied (PG16+)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(probe)
                conn.rollback()

    # 3. THE proof: a real bundle's whole lifecycle AS fleet_provisioner
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute(bundle.forward_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM organizations WHERE id = %s", (spec.org_uuid,))
            assert cur.fetchone() is not None
            cur.execute(f"SELECT count(*) FROM pg_policies "
                        "WHERE tablename IN (%s, %s)", (spec.content_table, spec.history_table))
            assert cur.fetchone()[0] >= 4  # every policy org-pinned (render contract)
        with conn.cursor() as cur:
            cur.execute(bundle.revert_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"public.{spec.content_table}",))
            assert cur.fetchone()[0] is None
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (spec.editor_role,))
            assert cur.fetchone() is None
            cur.execute("SELECT 1 FROM organizations WHERE id = %s", (spec.org_uuid,))
            assert cur.fetchone() is None

    # 4. re-forward + back-to-back second revert as the role (idempotent twin,
    #    cc-quality's #79063 sequence — now under the dedicated-role grants)
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute(bundle.forward_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(bundle.revert_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(bundle.revert_sql)
        conn.commit()
