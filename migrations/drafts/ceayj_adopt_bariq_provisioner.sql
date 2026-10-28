-- ceayj adopt-bariq-provisioner (orch-console #80291 plan, #80298 approval)
-- ONE-SHOT for the live ihsanos multi-tenant store shape read read-only on
-- 2026-10-28 (bus #80296): this migration ASSERTS that exact shape first and
-- refuses (named error, nothing touched) on any drift, then
--   (1) two fleet_provisioner-only RLS policies on organizations
--       (INSERT WITH CHECK marker / DELETE USING marker — no SELECT policy,
--        so no tenant read path widens for the role),
--   (2) adopts the manually-pre-applied bariq client artifacts (content +
--       history tables, audit function) to fleet_provisioner ownership —
--       the steady state a fresh bundle-create would have produced,
--   (3) marks the bariq org row with the managed_by marker the policies pin,
--   (4) creates provisioner_revert_snapshots — the verified-snapshot marker
--       table the rendered revert guard consults (ops metadata ONLY:
--       client_key, counts, sha256s, path, timestamp; never tenant data,
--       never credentials — TENANT-RESIDENCY-001 posture).
-- KNOWN LIMIT (#80298 A.1, accepted): a tenant org_admin can edit its own
-- org settings, so the marker is not tamper-proof against that tenant;
-- fleet_provisioner still acts only through gated verbs.
-- REQUIRES: fleet_provisioner exists (installer step 3 applies it) — the
-- assert below refuses instructively otherwise. No superuser-only statement
-- anywhere in this file (bus #80261 class): CREATE POLICY / ALTER TABLE
-- OWNER TO / UPDATE are ownership+CREATEROLE powers, which the ceayj admin
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

-- ==== 0. SHAPE ASSERTS — every object as read live; any drift refuses ====
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fleet_provisioner') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: fleet_provisioner does not exist — installer step 3 must land first (bus #80291)';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bariq_editor') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: bariq_editor missing — live shape drifted from the 2026-10-28 read';
  END IF;
END $$;

DO $$ BEGIN
  IF to_regclass('public.organizations') IS NULL THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: public.organizations missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname='public' AND tablename='organizations'
                    AND policyname IN ('Anyone can look up org by slug',
                                       'Org admins can update their own org',
                                       'Users can see their orgs')) THEN
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
  IF (SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='bariq_site_content')
       NOT IN (CURRENT_USER, 'fleet_provisioner')
     OR (SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='bariq_site_content_history')
       NOT IN (CURRENT_USER, 'fleet_provisioner') THEN
    RAISE EXCEPTION 'ADOPT-REFUSED: content/history owned by neither the admin (%) nor fleet_provisioner — adoption cannot run (and refuses rather than guess)', CURRENT_USER;
  END IF;
  -- shape via pg_attribute, NOT information_schema: after a completed
  -- adoption the admin owns nothing on these tables and information_schema
  -- columns are privilege-visible only — a re-run would see an empty array
  -- and wrongly refuse. pg_attribute is world-readable; format_type spells
  -- the same canonical names the live read pinned.
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
  IF NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname='public' AND tablename='bariq_site_content'
                    AND policyname IN ('bariq_site_content_public_read',
                                       'bariq_site_content_editor_select',
                                       'bariq_site_content_editor_update'))
     OR NOT EXISTS (SELECT 1 FROM pg_policies
                  WHERE schemaname='public' AND tablename='bariq_site_content_history'
                    AND policyname = 'bariq_site_content_history_editor_select') THEN
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

-- ==== 2. adoption: the client artifacts become fleet_provisioner's ====
-- grants and policies survive ownership changes; the site's readers
-- (anon SELECT policy, bariq_editor) are unaffected. Guarded on the CURRENT
-- owner (#80298 condition 2, idempotent): a re-run after a completed
-- adoption must be a no-op here, not an ownership error. ALTER ... OWNER TO
-- additionally requires being able to SET ROLE to the target (PG16+) — the
-- admin holds ADMIN OPTION on fleet_provisioner (it created the role) but
-- NOT membership, so it grants itself membership for the transfer window
-- and drops it right after (fail-closed: an admin without the grant option
-- refuses here rather than guessing).
DO $$ BEGIN
  EXECUTE $ddl$GRANT fleet_provisioner TO CURRENT_USER$ddl$;
  IF (SELECT tableowner FROM pg_tables WHERE schemaname='public'
       AND tablename='bariq_site_content') IS DISTINCT FROM 'fleet_provisioner' THEN
    EXECUTE $ddl$ALTER TABLE public.bariq_site_content OWNER TO fleet_provisioner$ddl$;
  END IF;
  IF (SELECT tableowner FROM pg_tables WHERE schemaname='public'
       AND tablename='bariq_site_content_history') IS DISTINCT FROM 'fleet_provisioner' THEN
    EXECUTE $ddl$ALTER TABLE public.bariq_site_content_history OWNER TO fleet_provisioner$ddl$;
  END IF;
  IF (SELECT pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname='public' AND p.proname='bariq_site_content_audit')
     IS DISTINCT FROM 'fleet_provisioner' THEN
    EXECUTE $ddl$ALTER FUNCTION public.bariq_site_content_audit() OWNER TO fleet_provisioner$ddl$;
  END IF;
  EXECUTE $ddl$REVOKE fleet_provisioner FROM CURRENT_USER$ddl$;
END $$;

-- ==== 2b. fp needs ADMIN OPTION on the pre-existing editor role ====
-- A fresh bundle-create would have it (fp CREATED bariq_editor there, and
-- PG16+ creators get admin on what they create); the adopted world's role
-- was created by the admin, so the forward's existence guard skips
-- creation and fp never gains admin — and the bundle's REVERT (DROP ROLE)
-- needs exactly that: CREATEROLE + ADMIN OPTION on the target. Without
-- this grant, a verified revert of the adopted client can never complete.
-- Idempotent: re-granting a held membership/admin option is a no-op.
GRANT bariq_editor TO fleet_provisioner WITH ADMIN OPTION;

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
