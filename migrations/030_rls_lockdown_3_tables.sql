-- 030_rls_lockdown_3_tables.sql — CAI-RESP-509: complete the RLS shape on the
-- last three public tables that still had ROW LEVEL SECURITY OFF.
--
-- cai already applied the URGENT half (write-REVOKE — anon has no DELETE on any of
-- the three). This migration finishes the FULL RLS shape, mirroring the sanctioned
-- pattern in migrations/013_substrate_rls_grant_lockdown.sql:
--
--   * fleet_stall_state  — FULL-LOCK. Internal fleet signal; must NOT be publicly
--     readable. Revoke anon/authenticated/PUBLIC entirely (incl. SELECT); RLS
--     deny-all to public (no non-service policy). service_role is the only writer.
--   * portfolio_entries  — PUBLICLY READABLE (rendered by the public site). Keep an
--     explicit RLS public-SELECT policy + table-level SELECT grant; service_role writes.
--   * site_content       — PUBLICLY READABLE (rendered by the public site). Same shape.
--
-- Hardening only: ENABLE RLS + REVOKE + policy create. No DROP TABLE/COLUMN/etc, so it
-- passes scripts/check_additive_migration.py. Idempotent (DROP POLICY IF EXISTS + GRANT).
-- Apply via scripts/apply_migration.py 030 --silo tscuymavysscrvoberrr (historical
-- applier: apply_rls_lockdown_030.py, deleted 2026-09-05 PR #90; decision-962: never `supabase db push`).
-- Transaction is owned by the applier (no BEGIN/COMMIT here) so --dry-run can ROLLBACK.

-- ── fleet_stall_state : FULL-LOCK (internal signal, no public read) ──────────
ALTER TABLE public."fleet_stall_state" ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "fleet_stall_state_service_only" ON public."fleet_stall_state";
CREATE POLICY "fleet_stall_state_service_only" ON public."fleet_stall_state" FOR ALL TO service_role USING (true) WITH CHECK (true);
REVOKE ALL ON public."fleet_stall_state" FROM anon, authenticated, PUBLIC;
GRANT ALL ON public."fleet_stall_state" TO service_role;

-- ── portfolio_entries : PUBLICLY READABLE (public site reads it) ─────────────
ALTER TABLE public."portfolio_entries" ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "portfolio_entries_service_only" ON public."portfolio_entries";
CREATE POLICY "portfolio_entries_service_only" ON public."portfolio_entries" FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "portfolio_entries_public_read" ON public."portfolio_entries";
CREATE POLICY "portfolio_entries_public_read" ON public."portfolio_entries" FOR SELECT TO anon, authenticated USING (true);
REVOKE ALL ON public."portfolio_entries" FROM anon, authenticated, PUBLIC;
GRANT SELECT ON public."portfolio_entries" TO anon, authenticated;  -- keep the public read working
GRANT ALL ON public."portfolio_entries" TO service_role;

-- ── site_content : PUBLICLY READABLE (public site reads it) ──────────────────
ALTER TABLE public."site_content" ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "site_content_service_only" ON public."site_content";
CREATE POLICY "site_content_service_only" ON public."site_content" FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "site_content_public_read" ON public."site_content";
CREATE POLICY "site_content_public_read" ON public."site_content" FOR SELECT TO anon, authenticated USING (true);
REVOKE ALL ON public."site_content" FROM anon, authenticated, PUBLIC;
GRANT SELECT ON public."site_content" TO anon, authenticated;  -- keep the public read working
GRANT ALL ON public."site_content" TO service_role;

-- ── Convention 4: assertion gate — verify intended effect, RAISE (rollback) if not ──
DO $gate$
DECLARE
  bad text;
BEGIN
  -- (1) RLS enabled on all three
  SELECT string_agg(c.relname, ', ') INTO bad
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND c.relname IN ('fleet_stall_state', 'portfolio_entries', 'site_content')
    AND c.relrowsecurity IS NOT TRUE;
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'RLS not enabled on: %', bad;
  END IF;

  -- (2) the two readable tables have a public-SELECT policy
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
                 AND tablename='portfolio_entries' AND policyname='portfolio_entries_public_read') THEN
    RAISE EXCEPTION 'missing portfolio_entries_public_read policy';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
                 AND tablename='site_content' AND policyname='site_content_public_read') THEN
    RAISE EXCEPTION 'missing site_content_public_read policy';
  END IF;

  -- (3) fleet_stall_state is FULLY LOCKED to public: no non-service policy,
  --     and no anon/authenticated table grants remain
  IF EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public'
             AND tablename='fleet_stall_state' AND policyname <> 'fleet_stall_state_service_only') THEN
    RAISE EXCEPTION 'fleet_stall_state has an unexpected public policy';
  END IF;
  SELECT string_agg(DISTINCT grantee, ', ') INTO bad
  FROM information_schema.role_table_grants
  WHERE table_schema='public' AND table_name='fleet_stall_state'
    AND grantee IN ('anon', 'authenticated', 'PUBLIC');
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'fleet_stall_state still grants to: %', bad;
  END IF;

  -- (4) no anon/authenticated DELETE on any of the three (write-lock intact)
  SELECT string_agg(table_name || '/' || grantee, ', ') INTO bad
  FROM information_schema.role_table_grants
  WHERE table_schema='public'
    AND table_name IN ('fleet_stall_state', 'portfolio_entries', 'site_content')
    AND grantee IN ('anon', 'authenticated', 'PUBLIC')
    AND privilege_type IN ('DELETE', 'INSERT', 'UPDATE', 'TRUNCATE');
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected public write grant on: %', bad;
  END IF;
END
$gate$;

-- ── Convention 3: forensic ledger (populated statements, not ARRAY[]::text[]) ──
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260722000000', '030_rls_lockdown_3_tables', ARRAY[
  $stmt$ALTER TABLE public.fleet_stall_state ENABLE ROW LEVEL SECURITY$stmt$,
  $stmt$CREATE POLICY fleet_stall_state_service_only ON public.fleet_stall_state FOR ALL TO service_role USING (true) WITH CHECK (true)$stmt$,
  $stmt$REVOKE ALL ON public.fleet_stall_state FROM anon, authenticated, PUBLIC$stmt$,
  $stmt$GRANT ALL ON public.fleet_stall_state TO service_role$stmt$,
  $stmt$ALTER TABLE public.portfolio_entries ENABLE ROW LEVEL SECURITY$stmt$,
  $stmt$CREATE POLICY portfolio_entries_service_only ON public.portfolio_entries FOR ALL TO service_role USING (true) WITH CHECK (true)$stmt$,
  $stmt$CREATE POLICY portfolio_entries_public_read ON public.portfolio_entries FOR SELECT TO anon, authenticated USING (true)$stmt$,
  $stmt$REVOKE ALL ON public.portfolio_entries FROM anon, authenticated, PUBLIC; GRANT SELECT ON public.portfolio_entries TO anon, authenticated; GRANT ALL ON public.portfolio_entries TO service_role$stmt$,
  $stmt$ALTER TABLE public.site_content ENABLE ROW LEVEL SECURITY$stmt$,
  $stmt$CREATE POLICY site_content_service_only ON public.site_content FOR ALL TO service_role USING (true) WITH CHECK (true)$stmt$,
  $stmt$CREATE POLICY site_content_public_read ON public.site_content FOR SELECT TO anon, authenticated USING (true)$stmt$,
  $stmt$REVOKE ALL ON public.site_content FROM anon, authenticated, PUBLIC; GRANT SELECT ON public.site_content TO anon, authenticated; GRANT ALL ON public.site_content TO service_role$stmt$
]::text[])
ON CONFLICT (version) DO NOTHING;
