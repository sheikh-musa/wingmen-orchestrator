-- ceayj adopt-bariq-provisioner (orch-console #80291 plan, #80298 approval,
-- #80366 redesign: NO ownership transfer, NO admin grant on the editor role)
--
-- ledger: silo=ceayjeamtmcyzzvqflus
-- (ihsanos multi-tenant DB — the ADOPTED store: this migration runs on the
-- store that already holds bariq's live artifacts, unlike 128 which ledgers
-- on the orchestrator substrate where the provisioner is invoked)
--
-- ONE-SHOT for the live ihsanos multi-tenant store shape read read-only on
-- 2026-10-28 (bus #80296): this migration ASSERTS that exact shape first and
-- refuses (named error, nothing touched) on any drift, then
--   (1) two fleet_provisioner-only RLS policies on organizations
--       (INSERT WITH CHECK marker / DELETE USING marker — no SELECT policy,
--        so no tenant read path widens for the role),
--   (2) syncs the ONE privilege the manual era left divergent from the
--       rendered posture (PUBLIC EXECUTE on the audit fn) — as the owner,
--       admin-side; fleet_provisioner never gains ownership of ANY live
--       client artifact (#80366: ownership cannot be returned by the
--       bundle revert, because PG16+ ALTER ... OWNER TO requires SET ROLE
--       to the target owner — so it must never leave),
--   (3) marks the bariq org row with the managed_by marker the policies pin,
--   (4) creates provisioner_revert_snapshots — the verified-snapshot marker
--       table the rendered revert guard consults (ops metadata ONLY:
--       client_key, counts, sha256s, path, timestamp; never tenant data,
--       never credentials — TENANT-RESIDENCY-001 posture),
--   (5) creates provisioner_adopted_objects — the adoption RECORD (#80366):
--       for each pre-existing object (editor role, both tables, audit fn,
--       org row) whether it was adopted (vs created by a bundle) and its
--       ORIGINAL OWNER read live at adoption, never typed. The rendered
--       bundle consults it at execute time: the forward SKIPS the
--       privilege/RLS block for adopted tables, and the revert SKIPS all
--       teardown for adopted objects (named notices) — the adopted editor
--       stays LOGIN, tables keep rows under their original owner, the
--       site read path (anon policy, trigger, fn) and the org row stay.
-- KNOWN LIMIT (#80298 A.1, accepted): a tenant org_admin can edit its own
-- org settings, so the marker is not tamper-proof against that tenant;
-- fleet_provisioner still acts only through gated verbs.
-- REQUIRES: fleet_provisioner exists (installer step 3 applies it) — the
-- assert below refuses instructively otherwise. No superuser-only statement
-- anywhere in this file (bus #80261 class): CREATE POLICY / UPDATE /
-- REVOKE-on-owned are ownership+CREATEROLE powers, which the ceayj admin
-- (postgres, CREATEROLE non-superuser) holds.
--
-- assert: no_table_privilege anon public.provisioner_revert_snapshots SELECT
-- assert: no_table_privilege anon public.provisioner_revert_snapshots INSERT
-- assert: no_table_privilege anon public.provisioner_revert_snapshots UPDATE
-- assert: no_table_privilege anon public.provisioner_revert_snapshots DELETE
-- assert: no_table_privilege anon public.provisioner_revert_snapshots TRUNCATE
-- assert: no_table_privilege authenticated public.provisioner_revert_snapshots SELECT
-- assert: no_table_privilege authenticated public.provisioner_revert_snapshots INSERT
-- assert: no_table_privilege authenticated public.provisioner_revert_snapshots UPDATE
-- assert: no_table_privilege authenticated public.provisioner_revert_snapshots DELETE
-- assert: no_table_privilege authenticated public.provisioner_revert_snapshots TRUNCATE
-- assert: no_table_privilege anon public.provisioner_adopted_objects SELECT
-- assert: no_table_privilege anon public.provisioner_adopted_objects INSERT
-- assert: no_table_privilege anon public.provisioner_adopted_objects UPDATE
-- assert: no_table_privilege anon public.provisioner_adopted_objects DELETE
-- assert: no_table_privilege anon public.provisioner_adopted_objects TRUNCATE
-- assert: no_table_privilege authenticated public.provisioner_adopted_objects SELECT
-- assert: no_table_privilege authenticated public.provisioner_adopted_objects INSERT
-- assert: no_table_privilege authenticated public.provisioner_adopted_objects UPDATE
-- assert: no_table_privilege authenticated public.provisioner_adopted_objects DELETE
-- assert: no_table_privilege authenticated public.provisioner_adopted_objects TRUNCATE

-- ==== 0. SHAPE ASSERTS — every object as read live; any drift refuses ====
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fleet_provisioner') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: fleet_provisioner does not exist — installer step 3 must land first (bus #80291)';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bariq_editor') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: bariq_editor missing — live shape drifted from the 2026-10-28 read';
  END IF;
  -- the editor is a LIVE LOGIN role in active use for Bariq's edit flow
  -- (#80315/#80319/#80366) — the bundle must never drop it or delog it
  IF NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'bariq_editor') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: bariq_editor is not LOGIN — it is in live use; live shape drifted';
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('public.organizations') IS NULL THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: public.organizations missing';
  END IF;
  -- COUNTED, not EXISTS-over-IN: `NOT EXISTS (... IN (a,b,c))` is true
  -- only at ZERO matches, so partial drift (one policy renamed or dropped
  -- between the live read and this apply) passed the guard (cc-quality
  -- #80372, condition-3 gap) — each named policy must be present.
  IF (SELECT count(*) FROM pg_policies
       WHERE schemaname='public' AND tablename='organizations'
         AND policyname IN ('Anyone can look up org by slug',
                            'Org admins can update their own org',
                            'Users can see their orgs')) <> 3 THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: the three live organizations policies are not all present';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.organizations
                  WHERE id = '62617269-7100-4000-8000-000000000001'
                    AND slug = 'bariq-employment' AND type = 'business'
                    AND deleted_at IS NULL) THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: the bariq org row (slug/type/deleted_at) drifted from the live read';
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('public.bariq_site_content') IS NULL OR to_regclass('public.bariq_site_content_history') IS NULL THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: content/history tables missing — not the adopted world this migration pins';
  END IF;
  -- shape via pg_attribute, NOT information_schema: information_schema
  -- columns are privilege-visible only, and this migration deliberately
  -- runs as an admin that may hold no privileges on these tables (#80366:
  -- ownership never moves). pg_attribute is world-readable; format_type
  -- spells the same canonical names the live read pinned.
  IF ARRAY(SELECT a.attname || ':' || format_type(a.atttypid, a.atttypmod)
             FROM pg_attribute a
            WHERE a.attrelid = 'public.bariq_site_content'::regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum)
     <> ARRAY['org_id:uuid','key:text','value:jsonb','updated_at:timestamp with time zone','updated_by:text'] THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: bariq_site_content column shape drifted from the render';
  END IF;
  IF ARRAY(SELECT a.attname || ':' || format_type(a.atttypid, a.atttypmod)
             FROM pg_attribute a
            WHERE a.attrelid = 'public.bariq_site_content_history'::regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum)
     <> ARRAY['id:bigint','org_id:uuid','key:text','old_value:jsonb','new_value:jsonb','changed_by:text','changed_at:timestamp with time zone'] THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: bariq_site_content_history column shape drifted from the render';
  END IF;
  -- counted individually for the same partial-drift reason as above
  IF (SELECT count(*) FROM pg_policies
       WHERE schemaname='public' AND tablename='bariq_site_content'
         AND policyname IN ('bariq_site_content_public_read',
                            'bariq_site_content_editor_select',
                            'bariq_site_content_editor_update')) <> 3
     OR (SELECT count(*) FROM pg_policies
          WHERE schemaname='public' AND tablename='bariq_site_content_history'
            AND policyname = 'bariq_site_content_history_editor_select') <> 1 THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: the four bundle-named policies are not all present';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname='public' AND p.proname='bariq_site_content_audit') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: bariq_site_content_audit() missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger
                  WHERE tgname='bariq_site_content_audit_trg' AND NOT tgisinternal) THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: audit trigger missing';
  END IF;
  IF to_regclass('public.migration_ledger') IS NULL THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: public.migration_ledger missing';
  END IF;
END $$;

-- the privilege/RLS POSTURE the rendered bundle establishes on a fresh
-- create, pinned here (read-only live probe 2026-10-29) because the
-- bundle forward SKIPS its privilege block for adopted tables (#80366) —
-- what stands live must already BE the rendered posture, or adoption
-- refuses rather than paper over the drift. (The one divergence — PUBLIC
-- EXECUTE on the audit fn — is synced in section 2 below.)
DO $$ BEGIN
  IF (SELECT relrowsecurity FROM pg_class WHERE oid='public.bariq_site_content'::regclass) IS NOT TRUE
     OR (SELECT relrowsecurity FROM pg_class WHERE oid='public.bariq_site_content_history'::regclass) IS NOT TRUE THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: RLS is not enabled on both content tables';
  END IF;
  IF NOT has_table_privilege('anon', 'public.bariq_site_content', 'SELECT')
     OR has_table_privilege('anon', 'public.bariq_site_content', 'INSERT')
     OR has_table_privilege('anon', 'public.bariq_site_content', 'UPDATE')
     OR has_table_privilege('anon', 'public.bariq_site_content', 'DELETE') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: anon privilege posture on bariq_site_content drifted (expected SELECT only)';
  END IF;
  IF NOT has_table_privilege('bariq_editor', 'public.bariq_site_content', 'SELECT')
     OR NOT has_table_privilege('bariq_editor', 'public.bariq_site_content', 'UPDATE')
     OR has_table_privilege('bariq_editor', 'public.bariq_site_content', 'INSERT')
     OR has_table_privilege('bariq_editor', 'public.bariq_site_content', 'DELETE') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: bariq_editor posture on bariq_site_content drifted (expected SELECT+UPDATE only)';
  END IF;
  IF has_table_privilege('anon', 'public.bariq_site_content_history', 'SELECT')
     OR has_table_privilege('anon', 'public.bariq_site_content_history', 'INSERT')
     OR has_table_privilege('anon', 'public.bariq_site_content_history', 'UPDATE')
     OR has_table_privilege('anon', 'public.bariq_site_content_history', 'DELETE')
     OR NOT has_table_privilege('bariq_editor', 'public.bariq_site_content_history', 'SELECT')
     OR has_table_privilege('bariq_editor', 'public.bariq_site_content_history', 'UPDATE')
     OR has_table_privilege('bariq_editor', 'public.bariq_site_content_history', 'DELETE') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: history privilege posture drifted (anon nothing, editor SELECT only)';
  END IF;
END $$;

-- ==== 1. organizations: fleet_provisioner-only, marker-pinned policies ====
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname='public' AND tablename='organizations'
                    AND policyname='organizations_fp_insert') THEN
    EXECUTE 'CREATE POLICY organizations_fp_insert ON public.organizations '
            'FOR INSERT TO fleet_provisioner '
            'WITH CHECK (settings->>''managed_by'' = ''wingmen_provisioner'')';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname='public' AND tablename='organizations'
                    AND policyname='organizations_fp_delete') THEN
    EXECUTE 'CREATE POLICY organizations_fp_delete ON public.organizations '
            'FOR DELETE TO fleet_provisioner '
            'USING (settings->>''managed_by'' = ''wingmen_provisioner'')';
  END IF;
END $$;

-- ==== 2. privilege sync (the ONLY admin-side artifact change, #80366) ====
-- NO ownership transfers, NO admin grants on the editor role: the earlier
-- draft transferred ownership to fleet_provisioner so the bundle's
-- REVOKE/GRANT/RLS block would run — but the bundle revert could then
-- never give ownership back (PG16+ ALTER ... OWNER TO requires SET ROLE
-- to the target owner, which fleet_provisioner must never hold), and an
-- admin grant on bariq_editor existed only to enable a DROP that would
-- take down Bariq's live editing. Instead: the artifacts stay exactly
-- where they are, the adoption record (section 5) tells the bundle to
-- skip what it must not touch, and the one live divergence from the
-- rendered posture — PUBLIC-default EXECUTE on the audit fn, never
-- revoked in the manual era — is synced here, by the owner.
REVOKE ALL ON FUNCTION public.bariq_site_content_audit() FROM anon, authenticated, PUBLIC;
DO $$ BEGIN
  IF has_function_privilege('anon', 'public.bariq_site_content_audit()', 'EXECUTE') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: audit fn privilege sync did not land';
  END IF;
END $$;

-- ==== 3. mark the bariq org row (idempotent; never overwrites a set marker) ====
UPDATE public.organizations
   SET settings = settings || '{"managed_by":"wingmen_provisioner"}'::jsonb
 WHERE id = '62617269-7100-4000-8000-000000000001'
   AND NOT settings ? 'managed_by';

-- ==== 4. verified-snapshot marker table (ops metadata only) ====
CREATE TABLE IF NOT EXISTS public.provisioner_revert_snapshots (
  client_key     TEXT        NOT NULL,
  content_rows   BIGINT      NOT NULL,
  history_rows   BIGINT      NOT NULL,
  content_sha256 TEXT        NOT NULL,
  history_sha256 TEXT        NOT NULL,
  snapshot_path  TEXT        NOT NULL,
  taken_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  verified       BOOLEAN     NOT NULL DEFAULT true,
  CONSTRAINT provisioner_revert_snapshots_pkey PRIMARY KEY (client_key, taken_at)
);
REVOKE ALL ON public.provisioner_revert_snapshots FROM anon, authenticated;
ALTER TABLE public.provisioner_revert_snapshots ENABLE ROW LEVEL SECURITY;
-- the one permissive ops-table policy, TO the fleet role alone: the daemon
-- (AS fleet_provisioner) writes the marker inside the same store txn as the
-- revert it authorizes, and the revert SQL's guard re-checks LIVE row counts
-- against it at execute time — a stale marker never authorizes anything.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname='public' AND tablename='provisioner_revert_snapshots'
                    AND policyname='provisioner_revert_snapshots_fp') THEN
    EXECUTE $ddl$CREATE POLICY provisioner_revert_snapshots_fp
      ON public.provisioner_revert_snapshots
      FOR ALL TO fleet_provisioner USING (true) WITH CHECK (true)$ddl$;
  END IF;
END $$;
GRANT SELECT, INSERT ON public.provisioner_revert_snapshots TO fleet_provisioner;

-- ==== 5. the adoption RECORD (#80366): created-vs-adopted + original owner ====
-- ops metadata ONLY (client key, object names, kinds, owner ROLE NAMES,
-- timestamp). The rendered bundle reads it at execute time: the forward
-- skips its privilege/RLS block for adopted tables; the revert skips ALL
-- teardown for adopted objects with named REVERT-SKIPPED-ADOPTED notices.
-- Seeds READ the live catalog (never typed), record only objects actually
-- adopted (owner IS DISTINCT FROM fleet_provisioner — a fresh
-- bundle-created client has nothing to record), and are idempotent.
CREATE TABLE IF NOT EXISTS public.provisioner_adopted_objects (
  client_key     TEXT        NOT NULL,
  object_name    TEXT        NOT NULL,
  object_kind    TEXT        NOT NULL,
  original_owner TEXT,
  adopted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT provisioner_adopted_objects_pkey PRIMARY KEY (client_key, object_name)
);
REVOKE ALL ON public.provisioner_adopted_objects FROM anon, authenticated;
ALTER TABLE public.provisioner_adopted_objects ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname='public' AND tablename='provisioner_adopted_objects'
                    AND policyname='provisioner_adopted_objects_fp') THEN
    EXECUTE $ddl$CREATE POLICY provisioner_adopted_objects_fp
      ON public.provisioner_adopted_objects
      FOR SELECT TO fleet_provisioner USING (true)$ddl$;
  END IF;
END $$;
GRANT SELECT ON public.provisioner_adopted_objects TO fleet_provisioner;

INSERT INTO public.provisioner_adopted_objects (client_key, object_name, object_kind, original_owner)
SELECT 'bariq', 'bariq_site_content', 'table', t.tableowner
  FROM pg_tables t
 WHERE t.schemaname='public' AND t.tablename='bariq_site_content'
   AND t.tableowner IS DISTINCT FROM 'fleet_provisioner'
ON CONFLICT (client_key, object_name) DO NOTHING;
INSERT INTO public.provisioner_adopted_objects (client_key, object_name, object_kind, original_owner)
SELECT 'bariq', 'bariq_site_content_history', 'table', t.tableowner
  FROM pg_tables t
 WHERE t.schemaname='public' AND t.tablename='bariq_site_content_history'
   AND t.tableowner IS DISTINCT FROM 'fleet_provisioner'
ON CONFLICT (client_key, object_name) DO NOTHING;
INSERT INTO public.provisioner_adopted_objects (client_key, object_name, object_kind, original_owner)
SELECT 'bariq', 'bariq_site_content_audit', 'function', pg_get_userbyid(p.proowner)
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname='bariq_site_content_audit'
   AND pg_get_userbyid(p.proowner) IS DISTINCT FROM 'fleet_provisioner'
ON CONFLICT (client_key, object_name) DO NOTHING;
INSERT INTO public.provisioner_adopted_objects (client_key, object_name, object_kind, original_owner)
SELECT 'bariq', 'bariq_editor', 'role', NULL
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bariq_editor')
ON CONFLICT (client_key, object_name) DO NOTHING;
INSERT INTO public.provisioner_adopted_objects (client_key, object_name, object_kind, original_owner)
SELECT 'bariq', 'organizations_row', 'org_row', NULL
 WHERE EXISTS (SELECT 1 FROM public.organizations
                WHERE id='62617269-7100-4000-8000-000000000001')
ON CONFLICT (client_key, object_name) DO NOTHING;
