"""PG17 integration — the rendered bundle actually applies, holds its
security posture, and reverts cleanly on a real ephemeral PostgreSQL 17
(finding 2's harness class; ceayj itself runs 17.6).

The ephemeral cluster is NOT Supabase: the harness mirrors the two things
the bundle assumes exist on the real store — the anon/authenticated roles
and the organizations table — then proves the rest is entirely the
bundle's. Skips cleanly when no PG17 binary is available (pg_dsn fixture).
"""
import sys
from pathlib import Path

import psycopg
import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner.render import render_bundle  # noqa: E402
from provisioner.spec import load_spec, spec_path  # noqa: E402

# mirrors what Supabase ships and the live ceayj store carries; the bundle's
# REVOKEs name these roles, so a vanilla cluster must stub them first
# idempotent: pg_dsn is SESSION-scoped — every test in this file shares one
# ephemeral cluster, so the prelude must survive re-running
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
    # teardown to a bare store: revert best-effort, then defensive drops —
    # a test may have died mid-forward, so nothing here may raise
    spec, bundle = _spec_bundle()
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        try:
            conn.execute(bundle.revert_sql)
        except Exception:
            pass
        conn.execute(f"DROP TABLE IF EXISTS public.{spec.history_table}")
        conn.execute(f"DROP TABLE IF EXISTS public.{spec.content_table}")
        try:
            conn.execute(f"REVOKE ALL ON SCHEMA public FROM {spec.editor_role}")
        except Exception:
            pass
        conn.execute(f"DROP ROLE IF EXISTS {spec.editor_role}")
        conn.execute("DELETE FROM organizations WHERE id = %s", (spec.org_uuid,))


def _spec_bundle():
    spec = load_spec(spec_path("bariq"))
    return spec, render_bundle(spec)


def test_bundle_applies_and_reverts_on_pg17(store):
    spec, bundle = _spec_bundle()
    with psycopg.connect(store) as conn:  # one txn per phase
        with conn.cursor() as cur:
            cur.execute(bundle.forward_sql)
        conn.commit()

        # org row landed with the lookup-free pin id
        row = conn.execute(
            "SELECT name, uen FROM organizations WHERE id = %s", (spec.org_uuid,)
        ).fetchone()
        assert row is not None and row[1] == spec.org_uen

        # tables + RLS enabled + org-pinned policies
        rls = conn.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = %s", (spec.content_table,)
        ).fetchone()
        assert rls and rls[0] is True
        pol = conn.execute(
            "SELECT count(*) FROM pg_policies "
            "WHERE tablename IN (%s, %s) AND qual LIKE %s",
            (spec.content_table, spec.history_table, f"%{spec.org_uuid}%"),
        ).fetchone()
        assert pol[0] >= 4  # every policy is org-pinned (render-time scan holds live)

        # migration_ledger bootstrapped; stays after revert (store infra)
        assert conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'migration_ledger'"
        ).fetchone()[0] == 1

        with conn.cursor() as cur:
            cur.execute(bundle.revert_sql)
        conn.commit()

        for gone in (spec.content_table, spec.history_table):
            assert conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = %s",
                (gone,),
            ).fetchone()[0] == 0
        # the org row is gone; the migration_ledger SURVIVES by design
        assert conn.execute(
            "SELECT count(*) FROM organizations WHERE id = %s", (spec.org_uuid,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'migration_ledger'"
        ).fetchone()[0] == 1
        # re-apply must work after a revert (idempotent lifecycle)
        with conn.cursor() as cur:
            cur.execute(bundle.forward_sql)
        conn.commit()
        # re-apply then DOUBLE revert back-to-back: the second revert is a
        # no-op, not an error (cc-quality Medium 2: REVOKE against a role
        # the first revert already dropped raised UndefinedObject — fails
        # closed in-server but is not idempotent)
        with conn.cursor() as cur:
            cur.execute(bundle.revert_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(bundle.revert_sql)
        conn.commit()


def test_editor_least_privilege_roundtrip_on_pg17(store):
    # the #77536 gold standard: verify by CONNECTING AS the role, never by
    # inspecting grants. GRANT without a policy = deny-all; only a real
    # roundtrip proves the policies admit the editor and nothing else.
    spec, bundle = _spec_bundle()
    with psycopg.connect(store) as conn:
        with conn.cursor() as cur:
            cur.execute(bundle.forward_sql)
        conn.commit()
        # login + a seed row (an admin's job, mirrors the live apply flow)
        conn.execute(f"ALTER ROLE {spec.editor_role} LOGIN")
        conn.execute(
            f"INSERT INTO {spec.content_table} (org_id, key, value, updated_by) "
            "VALUES (%s, 'site', '{\"v\":1}', 'admin') ON CONFLICT DO NOTHING",
            (spec.org_uuid,),
        )

    editor_dsn = store.replace("user=postgres", f"user={spec.editor_role}")
    with psycopg.connect(editor_dsn) as e:
        # SELECT: policy admits the org's rows
        assert e.execute(
            f"SELECT key FROM {spec.content_table} WHERE org_id = %s", (spec.org_uuid,)
        ).fetchone()[0] == "site"
        # UPDATE: allowed, in a txn we roll back (leaves no trace)
        v2 = '{"v":2}'
        e.execute(f"UPDATE {spec.content_table} SET value = '{v2}' "
                  "WHERE org_id = %s AND key = 'site'", (spec.org_uuid,))
        hist = e.execute(f"SELECT count(*) FROM {spec.history_table}").fetchone()[0]
        assert hist >= 1  # the audit trigger fired
        e.rollback()
        # INSERT/DELETE: denied — least privilege holds (table has no INSERT
        # grant for the editor, and no policy admits it either)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            e.execute(f"INSERT INTO {spec.content_table} (org_id, key, value) "
                      "VALUES (%s, 'x', '{}')", (spec.org_uuid,))
        e.rollback()  # a denied statement aborts the txn; reset between probes
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            e.execute(f"DELETE FROM {spec.content_table} WHERE org_id = %s", (spec.org_uuid,))
        e.rollback()
        # no reach beyond the client's own tables
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            e.execute("SELECT count(*) FROM organizations")


def test_createrole_cannot_escalate_to_superuser_pg17(store):
    # finding 2's negative pin (plan §8): on PG16+ a CREATEROLE holder must
    # NOT be able to grant itself superuser-class membership. If this ever
    # passes, the broker's server-major floor is load-bearing against a
    # live escalation, not just a version note.
    with psycopg.connect(store, autocommit=True) as conn:
        conn.execute("CREATE ROLE fleet_provisioner CREATEROLE LOGIN")
    with psycopg.connect(store.replace("user=postgres", "user=fleet_provisioner")) as fp:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            fp.execute("GRANT postgres TO fleet_provisioner")
        fp.rollback()  # a denied statement aborts the txn; reset between probes
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            fp.execute("SET ROLE postgres")
