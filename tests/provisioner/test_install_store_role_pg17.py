"""PG17 harness half of install_ceayj_provisioner step 3 + the adoption
bundle — proven by executing REAL bundles as fleet_provisioner on ephemeral
PostgreSQL 17 (ceayj itself runs 17.6).

Round 2 (#80291/#80298): the harness mirrors the LIVE store world, not a
blank canvas — organizations carries RLS plus the three live platform
policies (slug-lookup verbatim; the two auth.uid()-shaped ones over inert
stubs), the bariq artifacts (content/history tables with REAL rows, the
four bundle-named policies, audit fn + trigger, migration_ledger,
bariq_editor) pre-exist owned by the admin, exactly as the 2026-10-28
read-only live inspection found them. The admin side runs as the
NON-superuser CREATEROLE mirror of Supabase's postgres (_NonSuperAdmin);
everything the provisioner does happens AS the dedicated role. Skips
cleanly when no PG17 binary is available (pg_dsn fixture).
"""
import importlib.util
import sys
from pathlib import Path

import psycopg
import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner.render import render_bundle  # noqa: E402
from provisioner.snapshot import client_is_adopted, ensure_verified_snapshot  # noqa: E402
from provisioner.spec import load_spec, spec_path  # noqa: E402

_mod_path = ORCH_DIR / "scripts" / "install_ceayj_provisioner.py"
_ispec = importlib.util.spec_from_file_location("install_ceayj_provisioner", _mod_path)
inst = importlib.util.module_from_spec(_ispec)
_ispec.loader.exec_module(inst)

_ADOPT_DRAFT = (ORCH_DIR / "migrations" /
                "129_ceayj_adopt_bariq_provisioner.sql").read_text()


def test_migration_129_header_silo_matches_the_registry():
    """apply_migration refuses any file without a `-- ledger: silo=<ref>`
    header, and checks that ref against --silo AND the resolved DSN before
    touching anything. The ref is NEVER hand-typed (finding 1, #78463): it
    must equal the registry's alias->ref for this migration's target store."""
    from provisioner.registry import store_ref
    assert f"-- ledger: silo={store_ref('ihsanos multi-tenant DB')}" in _ADOPT_DRAFT, \
        "migration 129 lost its ledger silo header (or the ref drifted from the registry)"

# the LIVE organizations world (2026-10-28 read): the table, RLS on, the
# three platform policies — slug-lookup verbatim, the two auth.uid()-shaped
# ones over inert stubs (empty org_members, NULL auth.uid()) so their DDL
# and dependency shape are real while staying behaviorally inert for fp.
_HARNESS_PRELUDE = """
DROP SCHEMA IF EXISTS auth CASCADE;
CREATE SCHEMA auth;
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS $$ SELECT NULL::uuid $$;
CREATE FUNCTION auth.auth_user_org_ids() RETURNS SETOF uuid LANGUAGE sql
  AS $$ SELECT NULL::uuid WHERE false $$;
CREATE FUNCTION auth.auth_user_org_ids_with_role(text) RETURNS SETOF uuid LANGUAGE sql
  AS $$ SELECT NULL::uuid WHERE false $$;
DROP TABLE IF EXISTS org_members;
CREATE TABLE org_members (
  user_id uuid, org_id uuid, role text, deleted_at timestamptz);
DO $$ BEGIN CREATE ROLE anon NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP TABLE IF EXISTS organizations;
CREATE TABLE organizations (
  id       UUID PRIMARY KEY,
  name     TEXT NOT NULL,
  uen      TEXT,
  type     TEXT NOT NULL DEFAULT 'business',
  slug     TEXT,
  settings JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ
);
CREATE POLICY "Anyone can look up org by slug" ON organizations
  FOR SELECT TO public USING ((slug IS NOT NULL) AND (deleted_at IS NULL));
CREATE POLICY "Org admins can update their own org" ON organizations
  FOR UPDATE TO public USING ((deleted_at IS NULL) AND (id IN (SELECT om.org_id
  FROM org_members om WHERE ((om.user_id = auth.uid()) AND (om.role = 'org_admin'::text)
  AND (om.deleted_at IS NULL)))))
  WITH CHECK ((deleted_at IS NULL) AND (id IN (SELECT om.org_id
  FROM org_members om WHERE ((om.user_id = auth.uid()) AND (om.role = 'org_admin'::text)
  AND (om.deleted_at IS NULL)))));
CREATE POLICY "Users can see their orgs" ON organizations
  FOR SELECT TO public USING ((id IN (SELECT auth.auth_user_org_ids() AS auth_user_org_ids))
  AND ((deleted_at IS NULL) OR (id IN (SELECT auth.auth_user_org_ids_with_role('org_admin'::text)
  AS auth_user_org_ids_with_role))));
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
"""

# the pre-provisioner bariq artifacts exactly as they exist live: postgres
# (harness: the admin) owns everything, real content rows included. The
# privilege posture mirrors the 2026-10-29 read-only live probe: editor is
# LOGIN (in live use), anon reads content only, editor SELECT+UPDATE on
# content / SELECT on history, anon sees nothing of history, both tables
# RLS-enabled. The fn keeps the PUBLIC-default EXECUTE the live manual era
# never revoked — the migration's privilege-sync is what removes it.
_ADOPTED_WORLD = """
DROP TABLE IF EXISTS bariq_site_content_history;
DROP TABLE IF EXISTS bariq_site_content;
DROP FUNCTION IF EXISTS bariq_site_content_audit();
DROP TABLE IF EXISTS migration_ledger;
DO $$ BEGIN CREATE ROLE bariq_editor LOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE TABLE bariq_site_content (
  org_id     UUID NOT NULL REFERENCES organizations(id),
  key        TEXT NOT NULL,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL DEFAULT 'system',
  PRIMARY KEY (org_id, key)
);
CREATE TABLE bariq_site_content_history (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id     UUID NOT NULL REFERENCES organizations(id),
  key        TEXT NOT NULL,
  old_value  JSONB,
  new_value  JSONB NOT NULL,
  changed_by TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION bariq_site_content_audit() RETURNS TRIGGER AS $fn$
BEGIN
  INSERT INTO bariq_site_content_history (org_id, key, old_value, new_value, changed_by)
  VALUES (NEW.org_id, NEW.key, OLD.value, NEW.value, NEW.updated_by);
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;
CREATE TRIGGER bariq_site_content_audit_trg
AFTER UPDATE ON bariq_site_content
FOR EACH ROW EXECUTE FUNCTION bariq_site_content_audit();
CREATE POLICY bariq_site_content_public_read
  ON bariq_site_content FOR SELECT TO anon USING (org_id = '62617269-7100-4000-8000-000000000001');
CREATE POLICY bariq_site_content_editor_select
  ON bariq_site_content FOR SELECT TO bariq_editor USING (org_id = '62617269-7100-4000-8000-000000000001');
CREATE POLICY bariq_site_content_editor_update
  ON bariq_site_content FOR UPDATE TO bariq_editor USING (org_id = '62617269-7100-4000-8000-000000000001') WITH CHECK (org_id = '62617269-7100-4000-8000-000000000001');
CREATE POLICY bariq_site_content_history_editor_select
  ON bariq_site_content_history FOR SELECT TO bariq_editor USING (org_id = '62617269-7100-4000-8000-000000000001');
ALTER TABLE bariq_site_content ENABLE ROW LEVEL SECURITY;
ALTER TABLE bariq_site_content_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON bariq_site_content FROM anon, authenticated;
GRANT SELECT ON bariq_site_content TO anon;
GRANT SELECT, UPDATE ON bariq_site_content TO bariq_editor;
REVOKE ALL ON bariq_site_content_history FROM anon, authenticated;
GRANT SELECT ON bariq_site_content_history TO bariq_editor;
CREATE TABLE migration_ledger (
  repo           TEXT NOT NULL,
  migration_name TEXT NOT NULL,
  silo_ref       TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  applied_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_by     TEXT,
  note           TEXT,
  CONSTRAINT migration_ledger_pkey PRIMARY KEY (repo, migration_name, silo_ref)
);
ALTER TABLE migration_ledger ENABLE ROW LEVEL SECURITY;
INSERT INTO organizations (id, name, uen, type, slug, settings)
VALUES ('62617269-7100-4000-8000-000000000001', 'Bariq Employment Services Pte. Ltd.',
        '202219347M', 'business', 'bariq-employment', '{}');
INSERT INTO bariq_site_content (org_id, key, value, updated_by) VALUES
  ('62617269-7100-4000-8000-000000000001', 'hero', '{"h": "live"}', 'seed'),
  ('62617269-7100-4000-8000-000000000001', 'site', '{"s": 1}', 'seed'),
  ('62617269-7100-4000-8000-000000000001', 'testimonials', '{"t": []}', 'seed');
INSERT INTO bariq_site_content_history (org_id, key, old_value, new_value, changed_by) VALUES
  ('62617269-7100-4000-8000-000000000001', 'hero', NULL, '{"h": "live"}', 'seed');
"""

_SPEC = load_spec(spec_path("bariq"))
_BUNDLE = render_bundle(_SPEC)


def _spec_bundle():
    return _SPEC, _BUNDLE


def _fp(store):
    return store.replace("user=postgres", "user=fleet_provisioner")


class _NonSuperAdmin:
    """Mirrors Supabase's `postgres` role for the admin side: NOT a
    superuser, holds CREATEROLE, and owns (or holds grant options on) the
    objects it administers — including the pre-existing bariq artifacts,
    exactly as the live admin owns them (bus #80291). Every admin-side
    statement in this file runs inside this role."""

    def __init__(self, dsn, *, own_adopted=False):
        self.dsn = dsn
        self._own_adopted = own_adopted
        self._conn = None

    def __enter__(self):
        self._conn = psycopg.connect(self.dsn, autocommit=True)
        c = self._conn.cursor()
        c.execute('DO $$ BEGIN CREATE ROLE __harness_admin CREATEROLE NOLOGIN; '
                  'EXCEPTION WHEN duplicate_object THEN NULL; END $$')
        c.execute("ALTER TABLE public.organizations OWNER TO __harness_admin")
        if self._own_adopted:
            # the live admin owns the bariq artifacts — ownership it needs
            # for the adoption migration's privilege sync (the fn REVOKE) —
            # mirroring live, where postgres created and owns everything.
            # NO admin on bariq_editor anymore (#80366): the bundle must
            # never gain power over the live client role.
            for obj in ("ALTER TABLE public.bariq_site_content OWNER TO __harness_admin",
                        "ALTER TABLE public.bariq_site_content_history OWNER TO __harness_admin",
                        "ALTER TABLE public.migration_ledger OWNER TO __harness_admin",
                        "ALTER FUNCTION public.bariq_site_content_audit() OWNER TO __harness_admin",
                        "ALTER TABLE public.org_members OWNER TO __harness_admin"):
                c.execute(f"DO $$ BEGIN {obj}; EXCEPTION WHEN undefined_table THEN NULL; "
                          "WHEN undefined_function THEN NULL; END $$")
        c.execute("GRANT USAGE, CREATE ON SCHEMA public TO __harness_admin WITH GRANT OPTION")
        c.execute("SET ROLE __harness_admin")
        c.close()
        return self._conn

    def __exit__(self, *exc):
        c = self._conn.cursor()
        c.execute("RESET ROLE")
        c.execute("ALTER TABLE public.organizations OWNER TO postgres")
        # __harness_admin itself is deliberately LEFT in place: dropping it
        # while roles it granted (fleet_provisioner) exist fails on grant
        # dependencies, and the store teardown drops those only afterwards.
        # It is NOLOGIN, nothing references it, and re-entry tolerates the
        # duplicate (session-scoped ephemeral cluster).
        c.close()
        self._conn.close()
        return False


@pytest.fixture
def store(pg_dsn):
    from tests.conftest import assert_dsn_is_not_production

    assert_dsn_is_not_production(pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(_HARNESS_PRELUDE)
        conn.execute(_ADOPTED_WORLD)
    yield pg_dsn
    # teardown to a bare store (session-scoped cluster — nothing may raise)
    spec, bundle = _spec_bundle()
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for stmt in (bundle.revert_sql,
                     "DROP TABLE IF EXISTS public.provisioner_revert_snapshots",
                     "DROP TABLE IF EXISTS public.provisioner_adopted_objects"):
            try:
                conn.execute(stmt)
            except Exception:
                pass
        conn.execute(f"DROP TABLE IF EXISTS public.{spec.history_table}")
        conn.execute(f"DROP TABLE IF EXISTS public.{spec.content_table}")
        conn.execute("DROP FUNCTION IF EXISTS public.bariq_site_content_audit()")  # admin-owned post-#80366
        conn.execute("DROP TABLE IF EXISTS public.migration_ledger")  # store infra
        conn.execute("DROP TABLE IF EXISTS public.__fp_install_canary")
        # grantor-aware role cleanup, every statement defensive (a teardown
        # must never raise): schema-public grants were issued BY their
        # grantors — __harness_admin granted fleet_provisioner (WITH GRANT
        # OPTION), fleet_provisioner granted bariq_editor — so each REVOKE
        # runs AS its grantor with CASCADE for the dependent grant chain;
        # table grants die with their owner-side revokes below. A mid-render
        # failure (the RED state) rolls CREATE ROLE back too, so roles may
        # legitimately not exist.
        def _try(stmt):
            try:
                conn.execute(stmt)
            except Exception:
                pass

        def _exists(role):
            return conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()

        if _exists("fleet_provisioner") and _exists(spec.editor_role):
            _try("SET ROLE fleet_provisioner")
            _try(f"REVOKE ALL ON SCHEMA public FROM {spec.editor_role} CASCADE")
            _try("RESET ROLE")
        if _exists("__harness_admin"):
            _try("SET ROLE __harness_admin")
            for role in (spec.editor_role, "fleet_provisioner"):
                if _exists(role):
                    _try(f"REVOKE ALL ON SCHEMA public FROM {role} CASCADE")
            _try("RESET ROLE")
        for role in (spec.editor_role, "fleet_provisioner"):
            if _exists(role):
                _try(f"REVOKE INSERT, DELETE, REFERENCES ON TABLE public.organizations FROM {role}")
                _try(f"REVOKE SELECT (id) ON TABLE public.organizations FROM {role}")
        # fp's org policies must go BEFORE DROP ROLE fleet_provisioner — a
        # role that is a policy's target cannot be dropped
        _try("DROP POLICY IF EXISTS organizations_fp_insert ON public.organizations")
        _try("DROP POLICY IF EXISTS organizations_fp_delete ON public.organizations")
        for role in (spec.editor_role, "fleet_provisioner"):
            conn.execute(f"DROP ROLE IF EXISTS {role}")


def _fp_ready(store):
    """Admin side of steps 3 + the adoption migration, as the non-superuser
    admin: grants render -> attribute assert -> mint -> migration 129."""
    import secrets as _secrets

    pw = "FpMint_" + _secrets.token_urlsafe(12)
    with _NonSuperAdmin(store, own_adopted=True) as admin:
        admin.execute(inst.render_store_grants())
        inst.assert_role_not_elevated(admin.cursor(), "fleet_provisioner")
        inst._set_role_password(admin.cursor(), "fleet_provisioner", pw)
        admin.execute(_ADOPT_DRAFT)  # the gated migration, byte-identical draft
    return _fp(store)


# ---------------------------------------------------------------------------
# the shape of the live failure (bus #80291) — must fail WITHOUT the fixes
# ---------------------------------------------------------------------------

def test_canary_without_adoption_hits_the_live_rls_denial(store):
    """RED anchor (the live apply's exact failure): with only the step-3
    grants minted — no migration 129 — the canary's org INSERT dies on RLS
    exactly as it did on ceayj (InsufficientPrivilege, row-level security)."""
    import secrets as _secrets

    pw = "FpMint_" + _secrets.token_urlsafe(12)
    with _NonSuperAdmin(store, own_adopted=True) as admin:
        admin.execute(inst.render_store_grants())
        inst.assert_role_not_elevated(admin.cursor(), "fleet_provisioner")
        inst._set_role_password(admin.cursor(), "fleet_provisioner", pw)
    with psycopg.connect(_fp(store)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            with conn.cursor() as cur:
                cur.execute(inst.render_install_canary())
        conn.rollback()


def test_forward_over_adopted_world_is_idempotent_not_duplicatetable(store):
    fp = _fp_ready(store)
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute(_BUNDLE.forward_sql)   # adopted: every guard ACCEPTS
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(_BUNDLE.forward_sql)   # and a second run too
        conn.commit()
    # fp holds NO read path on the adopted tables (deliberately, #80366) —
    # the data read runs admin-side
    with psycopg.connect(store, autocommit=True) as admin:
        n = admin.execute("SELECT count(*) FROM bariq_site_content").fetchone()[0]
    assert n == 3, "adopted content rows must survive the forward verbatim"


def test_adoption_never_transfers_ownership_or_privileges(store):
    """#80366: fp must NOT own the live client artifacts — ownership stays
    with the original owner through adoption AND forward, and the privilege
    posture the migration synced is what stands afterwards (the forward
    skips its REVOKE/GRANT/RLS block for adopted objects)."""
    fp = _fp_ready(store)
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute(_BUNDLE.forward_sql)
        conn.commit()
    with psycopg.connect(store, autocommit=True) as admin:
        for tbl in ("bariq_site_content", "bariq_site_content_history"):
            owner = admin.execute(
                "SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename=%s",
                (tbl,)).fetchone()[0]
            assert owner == "__harness_admin", \
                f"{tbl} ownership left the original owner (now {owner})"
        fn_owner = admin.execute(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='bariq_site_content_audit'"
        ).fetchone()[0]
        assert fn_owner == "__harness_admin", "audit fn ownership left the original owner"
        # the posture the forward would establish is already standing —
        # asserted by the migration; here: the fn sync removed PUBLIC exec
        assert admin.execute(
            "SELECT has_function_privilege('anon', 'public.bariq_site_content_audit()', 'EXECUTE')"
        ).fetchone()[0] is False, "fn privilege sync did not remove anon EXECUTE"


def test_adoption_records_original_owners_read_live(store):
    """#80366: the adoption record is READ live at migration time, never
    typed — object set exact, owners equal the live catalog values."""
    _fp_ready(store)
    with psycopg.connect(store, autocommit=True) as admin:
        rows = admin.execute(
            "SELECT object_name, object_kind, original_owner "
            "FROM public.provisioner_adopted_objects WHERE client_key='bariq' "
            "ORDER BY object_name").fetchall()
    recorded = {r[0]: (r[1], r[2]) for r in rows}
    assert set(recorded) == {"bariq_editor", "bariq_site_content",
                             "bariq_site_content_history",
                             "bariq_site_content_audit", "organizations_row"}
    assert recorded["bariq_editor"] == ("role", None)
    assert recorded["organizations_row"] == ("org_row", None)
    with psycopg.connect(store, autocommit=True) as admin:
        for tbl in ("bariq_site_content", "bariq_site_content_history"):
            live_owner = admin.execute(
                "SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename=%s",
                (tbl,)).fetchone()[0]
            assert recorded[tbl] == ("table", live_owner), \
                f"{tbl}: recorded owner is not the live catalog owner"
        live_fn = admin.execute(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='bariq_site_content_audit'"
        ).fetchone()[0]
        assert recorded["bariq_site_content_audit"] == ("function", live_fn)


def test_forward_refuses_a_same_name_policy_with_a_different_expression(store):
    """#80298 condition 3: exact-shape guards — a drifted policy REFUSES.
    The drift is introduced ADMIN-side: fp owns nothing post-#80366."""
    _fp_ready(store)
    with psycopg.connect(store, autocommit=True) as admin:
        admin.execute("DROP POLICY bariq_site_content_public_read ON bariq_site_content")
        admin.execute("CREATE POLICY bariq_site_content_public_read "
                      "ON bariq_site_content FOR SELECT TO anon USING (true)")
    with psycopg.connect(_fp(store)) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="SHAPE-REFUSED.*public_read"):
            with conn.cursor() as cur:
                cur.execute(_BUNDLE.forward_sql)
        conn.rollback()


# ---------------------------------------------------------------------------
# #80298 condition 1 + #80366: the two revert worlds
#   adopted  -> revert SKIPS teardown for adopted objects (rows/owner/role
#               stay; snapshot is the belt, not the enabler)
#   fresh    -> full teardown, still gated by the verified-snapshot refusal
# ---------------------------------------------------------------------------

def test_adopted_revert_leaves_the_live_surface_intact(store):
    """The console-#80366 end state: after adopting a pre-existing
    bariq_editor and admin-owned tables, a bundle revert leaves the editor
    in place AND LOGIN, the tables with rows under their original owner,
    the site read path (policies, trigger, fn) and the org row untouched.
    The snapshot belt is SKIPPED for adopted clients (fp holds no read path
    on their tables — the belt arms the destructive path only)."""
    fp = _fp_ready(store)
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute(_BUNDLE.forward_sql)
            assert client_is_adopted(cur, _SPEC) is True
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(_BUNDLE.revert_sql)
        conn.commit()
    with psycopg.connect(store, autocommit=True) as admin:
        n = admin.execute("SELECT count(*) FROM bariq_site_content").fetchone()[0]
        editor = admin.execute(
            "SELECT rolcanlogin FROM pg_roles WHERE rolname='bariq_editor'").fetchone()
        org = admin.execute(
            "SELECT 1 FROM organizations WHERE id='62617269-7100-4000-8000-000000000001'"
        ).fetchone()
        pols = admin.execute(
            "SELECT count(*) FROM pg_policies WHERE tablename='bariq_site_content'").fetchone()[0]
        trg = admin.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname='bariq_site_content_audit_trg' "
            "AND NOT tgisinternal").fetchone()[0]
        owner = admin.execute(
            "SELECT tableowner FROM pg_tables WHERE schemaname='public' "
            "AND tablename='bariq_site_content'").fetchone()[0]
        fn_gone = admin.execute(
            "SELECT to_regprocedure('public.bariq_site_content_audit()') IS NULL").fetchone()[0]
        before_after = admin.execute(
            "SELECT count(*) FROM bariq_site_content_history").fetchone()[0]
    assert n == 3, "adopted revert dropped or touched content rows"
    assert editor == (True,), "adopted revert must leave bariq_editor in place, still LOGIN"
    assert org is not None, "adopted revert deleted the live org row"
    assert pols == 3 and trg == 1, "adopted revert tore down the site read path"
    assert owner == "__harness_admin", "adopted revert changed table ownership"
    assert fn_gone is False, "adopted revert dropped the audit fn"
    assert before_after == 1, "adopted revert mutated history"


def test_fresh_cycle_revert_refuses_without_snapshot_then_drops_and_restores(store, tmp_path):
    """Condition 1 on the DESTRUCTIVE path: a fresh (non-adopted) client
    with rows — revert refuses without a verified snapshot; with one it
    drops, and the snapshot restores byte-identical."""
    fp = _fp_ready(store)
    # de-adopt: clear the adoption record + drop the adopted artifacts so
    # the next forward is a FRESH create (fp owns what it creates)
    with psycopg.connect(store, autocommit=True) as admin:
        admin.execute("DELETE FROM public.provisioner_adopted_objects WHERE client_key='bariq'")
        admin.execute("DROP TABLE bariq_site_content_history")
        admin.execute("DROP TABLE bariq_site_content")
        admin.execute("DROP FUNCTION public.bariq_site_content_audit()")  # trigger died with its table
        # the adopted editor is admin-created — drop it too (its table grants
        # died with the tables; nothing else granted yet), so the fresh
        # forward re-creates it NOLOGIN as fp-owned and the fresh revert can
        # lawfully drop it again
        admin.execute("DROP ROLE bariq_editor")
        admin.execute("DELETE FROM organizations WHERE id='62617269-7100-4000-8000-000000000001'")
    with psycopg.connect(fp) as conn:
        with conn.cursor() as cur:
            cur.execute(_BUNDLE.forward_sql)   # fresh create as fp
            cur.execute("INSERT INTO bariq_site_content (org_id, key, value, updated_by) VALUES "
                        "('62617269-7100-4000-8000-000000000001', 'hero', '{\"h\": 1}', 'seed'), "
                        "('62617269-7100-4000-8000-000000000001', 'site', '{\"s\": 2}', 'seed')")
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="REVERT-REFUSED"):
            with conn.cursor() as cur:
                cur.execute(_BUNDLE.revert_sql)
        conn.rollback()
        with conn.cursor() as cur:
            assert cur.execute("SELECT count(*) FROM bariq_site_content").fetchone()[0] == 2
            before = {t: _capture(cur, t, o) for t, o in
                      (("bariq_site_content", "org_id, key"),
                       ("bariq_site_content_history", "id"))}
            info = ensure_verified_snapshot(cur, _SPEC, tmp_path)
            assert info["content_rows"] == 2 and info["skipped"] is False
            cur.execute(_BUNDLE.revert_sql)
        conn.commit()
        with conn.cursor() as cur:
            assert cur.execute(
                "SELECT to_regclass('public.bariq_site_content')").fetchone()[0] is None
            # restore from the snapshot files: re-create via the forward's
            # own DDL, COPY the bytes back, re-capture — byte-identical.
            # (history's id is GENERATED ALWAYS and COPY cannot override a
            # generated column — identity dropped for the restore only.)
            cur.execute(_BUNDLE.forward_sql)
            cur.execute("ALTER TABLE public.bariq_site_content_history "
                        "ALTER COLUMN id DROP IDENTITY IF EXISTS")
            after = {}
            for table in ("bariq_site_content", "bariq_site_content_history"):
                # empty tables have no snapshot file — they restore as the
                # empty streams they were captured as
                if list((tmp_path / "provisioner_snapshots").glob(f"*.{table}.copy")):
                    _restore(cur, tmp_path, table)
                after[table] = _capture(cur, table, "org_id, key" if "history" not in table else "id")
        conn.commit()
    assert after == before, "snapshot restore is not byte-identical"


def _capture(cur, table, order_by):
    with cur.copy(f"COPY (SELECT * FROM public.{table} ORDER BY {order_by}) TO STDOUT") as cp:
        return b"".join(bytes(chunk) for chunk in cp)


def _restore(cur, tmp_path, table):
    files = sorted((tmp_path / "provisioner_snapshots").glob(f"*.{table}.copy"))
    assert files, f"no snapshot file for {table}"
    with cur.copy(f"COPY public.{table} FROM STDIN") as cp:
        cp.write(files[-1].read_bytes())


def test_adoption_migration_is_idempotent(store):
    """#80298 condition 2: the draft re-runs clean over a COMPLETED adoption
    (marker set, policies present, adoption record seeded) — no-op, no
    error — and the adopted surface still works afterwards."""
    _fp_ready(store)  # first adoption
    # second run mirrors the LIVE re-run: ownership never moved (#80366),
    # so the admin needs no re-ownership crutch — but the fn privilege
    # sync must still be issuable (it owns the fn throughout, as live)
    with _NonSuperAdmin(store, own_adopted=False) as admin:
        admin.execute(_ADOPT_DRAFT)  # second run over the adopted state
    with psycopg.connect(_fp(store)) as conn:  # surface intact
        with conn.cursor() as cur:
            cur.execute(inst.render_install_canary())
        conn.rollback()


@pytest.mark.parametrize("drop_stmt,refuse_text", [
    # partial drift on the three platform org policies: ONE gone (renamed
    # or dropped between the live read and the apply) must refuse
    ('DROP POLICY "Users can see their orgs" ON public.organizations',
     "the three live organizations policies"),
    # partial drift inside the four-bundle-policy assert: one content
    # policy gone while the history policy stands
    ("DROP POLICY bariq_site_content_editor_update ON public.bariq_site_content",
     "the four bundle-named policies"),
])
def test_adoption_refuses_partial_policy_drift(store, drop_stmt, refuse_text):
    """cc-quality #80372 REQUIRED fix (condition-3 gap): the section-0
    presence asserts must refuse when ONE named policy is missing, not only
    when ALL are — `NOT EXISTS (... policyname IN (a,b,c))` is true only at
    ZERO matches, so partial drift sailed through. Each named policy is now
    counted individually."""
    import secrets as _secrets

    pw = "FpMint_" + _secrets.token_urlsafe(12)
    with _NonSuperAdmin(store, own_adopted=True) as admin:
        admin.execute(inst.render_store_grants())
        inst._set_role_password(admin.cursor(), "fleet_provisioner", pw)
        admin.execute(drop_stmt)  # the drift: one policy, not all
        with pytest.raises(psycopg.Error, match=refuse_text):
            admin.execute(_ADOPT_DRAFT)


# ---------------------------------------------------------------------------
# the full step-3 surface, now over the adopted world
# ---------------------------------------------------------------------------

def test_store_grants_are_idempotent(store):
    with _NonSuperAdmin(store) as admin:
        admin.execute(inst.render_store_grants())
        admin.execute(inst.render_store_grants())  # step-3 re-run is a no-op
        inst.assert_role_not_elevated(admin.cursor(), "fleet_provisioner")


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
        # fleet_provisioner is owned by the store fixture's teardown -- only
        # the log role is this test's exclusive residue
        with psycopg.connect(store, autocommit=True) as conn:
            conn.execute("DROP ROLE IF EXISTS provisioner_log_role")


def test_grant_set_is_exactly_the_bundles_surface_on_pg17(store):
    spec, bundle = _spec_bundle()
    fp = _fp_ready(store)

    # 1. the transactional install canary, AS the role: full surface, no
    #    residue — now INCLUDING the marker-pinned org INSERT/DELETE through
    #    the fp policies (the live apply's blocker, #80291)
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
                          "GRANT postgres TO fleet_provisioner",     # escalation — denied (PG16+)
                          "INSERT INTO organizations (id, name, type, settings) VALUES "  # unmarked — RLS
                          "('00000000-0000-0000-0000-00000000beef', 'x', 'business', '{}')"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(probe)
                conn.rollback()
            # an UNMARKED org row is invisible to fp's DELETE (USING never
            # matches): zero rows affected, silently — never a mass-delete
            # across other tenants' rows. (The INSERT-side pin already ran in
            # the probe loop above: an unmarked INSERT is an RLS ERROR.)
            with psycopg.connect(store, autocommit=True) as admin:
                admin.execute("INSERT INTO organizations (id, name, type, slug, settings) VALUES "
                              "('11111111-1111-1111-1111-111111111111', 'other tenant', "
                              "'business', 'other', '{}')")
            cur.execute("DELETE FROM organizations WHERE id = '11111111-1111-1111-1111-111111111111'")
            assert cur.rowcount == 0, "fp's DELETE touched an unmarked (other-tenant) org row"
            conn.rollback()

    # 3. THE proof: a real bundle's whole lifecycle AS fleet_provisioner over
    #    the ADOPTED world (content rows present -> snapshot-verified revert)
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
        conn.commit()
        # settings is NOT readable as fp (only id is granted — that grant set
        # is itself part of what this test pins), so the marker is read admin-side
        with psycopg.connect(store, autocommit=True) as admin:
            marked = admin.execute("SELECT settings->>'managed_by' FROM organizations "
                                   "WHERE id = %s", (spec.org_uuid,)).fetchone()
            assert marked == ("wingmen_provisioner",), "adoption marker missing on the org row"
        # revert of a populated ADOPTED client (#80366): the snapshot belt
        # is SKIPPED (adopted — same check the daemon makes) and the revert
        # leaves the adopted surface intact — teardown SKIP, not drop
        with conn.cursor() as cur:
            assert client_is_adopted(cur, spec) is True
            cur.execute(bundle.revert_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(f"SELECT to_regclass(%s)", (f"public.{spec.content_table}",))
            assert cur.fetchone()[0] is not None, "adopted revert dropped the client table"
            cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (spec.editor_role,))
            assert cur.fetchone() == (True,), "adopted revert dropped or delogged the editor"
            cur.execute("SELECT 1 FROM organizations WHERE id = %s", (spec.org_uuid,))
            assert cur.fetchone() is not None, "adopted revert deleted the org row"
