-- irsyad_monitor_ro: narrowed read-only role for the irsyad monitors (goumlyne).
-- Refs: console #81213 item 4; console rulings #82720 (Q1..Q3).
-- silos: goumlyne-only. NEVER apply on ceayj (different tenant; the org id below does not live there).
-- Owner: orch-console gates; cc-irsyad-coord applies via the gated exec path.
--
-- PURPOSE: the gzbai monitors (PII-containment monitor cron :23, Cisco reconcile watch cron :37,
--   weekly audit-chain check) read goumlyne through GOUMLYNE_RO_DATABASE_URL, which today is
--   auditor_ro (BYPASSRLS + pg_read_all_data = every row of every table on the silo). This role
--   replaces that DSN for the monitors with the minimum they need:
--     * SELECT on exactly 7 tables: audit_log, persons, sch_students, sch_student_parents,
--       tabung_coin_deposits, ui_events, org_members. Whole-table grants (not column grants) so
--       the PII monitor's information_schema.columns discovery still sees every column.
--     * Row visibility confined to the irsyad org (73339164-7c1f-40ba-a093-33f1f292dd4c) by a
--       PERMISSIVE + RESTRICTIVE policy pair per table, scoped TO irsyad_monitor_ro only.
--       The RESTRICTIVE policy is the guarantee: every pre-existing SELECT policy on these tables
--       applies TO public (so it also applies to this role); the restrictive one is ANDed with
--       all of them, so nothing outside the irsyad org can ever become visible. The PERMISSIVE
--       one is what makes the irsyad rows visible at all (including soft-deleted rows, which the
--       monitors must count).
--     * ui_events has no org_id: scoped to user_id IN (irsyad org_members.user_id). NULL user_id
--       rows are excluded by construction (console ruling Q3).
--   auditor_ro is NOT touched here. It stays broad, for daily_backup.sh and the agent-exec
--   reconcile only (see irsyad_monitor_ro_RUNBOOK.md).
--
-- PASSWORD / LOGIN: this file creates the role NOLOGIN and never sets a password. LOGIN and the
--   password are set LATER, out-of-band, by the secrets holder with a separate statement:
--     ALTER ROLE irsyad_monitor_ro LOGIN PASSWORD '<generated>';
--   Never commit a password to this file. Re-applying this file does not change LOGIN or the
--   password (the CREATE is guarded; attributes are asserted, not re-set).
--
-- REQUIRED EXTRA GRANTS (verified live on goumlyne): the existing PUBLIC SELECT policy on persons
--   calls three SECURITY DEFINER helpers that have NO PUBLIC EXECUTE (REVOKE ALL FROM PUBLIC in
--   migs 192/228/229/291/455), and several policies call auth.uid(), which needs USAGE on schema
--   auth (PUBLIC lacks it). Without these, every SELECT by this role on persons fails with
--   "permission denied for function". auth.uid() itself is EXECUTE-able by PUBLIC. With no JWT
--   claims on the monitor's connection, auth.uid() is NULL, so the helpers return empty sets and
--   the PUBLIC policies grant nothing; visibility comes only from the policies below.
--   Helper signatures (all zero-arg, RETURNS SETOF uuid, SECURITY DEFINER), latest definitions:
--     public.auth_nonadmin_staff_student_person_ids()  ihsanos supabase/migrations/464_authz_helpers_search_path_hardening.sql:94
--     public.auth_subadmin_student_person_ids()        ihsanos supabase/migrations/464_authz_helpers_search_path_hardening.sql:143
--     public.auth_user_subadmin_org_ids()              ihsanos supabase/migrations/226_subadmin_umum_tins_rls.sql:85
--
-- APPLY NOTE: no BEGIN/COMMIT of its own (the exec path wraps one transaction and refuses
--   top-level transaction control). Idempotent. Kept byte-identical inside
--   irsyad_monitor_ro_qa_proof.sql section (a): change both together.

-- 1. Role (guarded CREATE; attributes are asserted at the end, never silently altered).
DO $role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'irsyad_monitor_ro') THEN
    CREATE ROLE irsyad_monitor_ro
      NOLOGIN
      NOSUPERUSER
      NOBYPASSRLS
      NOINHERIT
      NOCREATEDB
      NOCREATEROLE
      NOREPLICATION;
  END IF;
END
$role$;

-- 2. Every session of this role is read-only by default (belt-and-braces on top of SELECT-only grants).
ALTER ROLE irsyad_monitor_ro SET default_transaction_read_only = on;

-- 3. Must NOT be a member of pg_read_all_data or of any other role.
DO $nomember$
DECLARE
  v_n integer;
BEGIN
  SELECT count(*) INTO v_n
  FROM pg_auth_members m
  JOIN pg_roles r ON r.oid = m.member
  WHERE r.rolname = 'irsyad_monitor_ro';
  IF v_n <> 0 THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_pre: role is a member of % role(s) (e.g. pg_read_all_data); revoke them first, refusing', v_n;
  END IF;
END
$nomember$;

-- 4. Grants.
GRANT USAGE ON SCHEMA public TO irsyad_monitor_ro;
GRANT USAGE ON SCHEMA auth TO irsyad_monitor_ro;

GRANT SELECT ON TABLE public.audit_log            TO irsyad_monitor_ro;
GRANT SELECT ON TABLE public.persons              TO irsyad_monitor_ro;
GRANT SELECT ON TABLE public.sch_students         TO irsyad_monitor_ro;
GRANT SELECT ON TABLE public.sch_student_parents  TO irsyad_monitor_ro;
GRANT SELECT ON TABLE public.tabung_coin_deposits TO irsyad_monitor_ro;
GRANT SELECT ON TABLE public.ui_events            TO irsyad_monitor_ro;
GRANT SELECT ON TABLE public.org_members          TO irsyad_monitor_ro;

GRANT EXECUTE ON FUNCTION public.auth_nonadmin_staff_student_person_ids() TO irsyad_monitor_ro;
GRANT EXECUTE ON FUNCTION public.auth_subadmin_student_person_ids()       TO irsyad_monitor_ro;
GRANT EXECUTE ON FUNCTION public.auth_user_subadmin_org_ids()             TO irsyad_monitor_ro;

-- 5. Row confinement: PERMISSIVE (makes irsyad rows visible) + RESTRICTIVE (guarantees nothing else is).
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.audit_log;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.audit_log;
CREATE POLICY irsyad_monitor_ro_org_permissive  ON public.audit_log AS PERMISSIVE  FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);
CREATE POLICY irsyad_monitor_ro_org_restrictive ON public.audit_log AS RESTRICTIVE FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);

DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.persons;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.persons;
CREATE POLICY irsyad_monitor_ro_org_permissive  ON public.persons AS PERMISSIVE  FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);
CREATE POLICY irsyad_monitor_ro_org_restrictive ON public.persons AS RESTRICTIVE FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);

DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.sch_students;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.sch_students;
CREATE POLICY irsyad_monitor_ro_org_permissive  ON public.sch_students AS PERMISSIVE  FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);
CREATE POLICY irsyad_monitor_ro_org_restrictive ON public.sch_students AS RESTRICTIVE FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);

DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.sch_student_parents;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.sch_student_parents;
CREATE POLICY irsyad_monitor_ro_org_permissive  ON public.sch_student_parents AS PERMISSIVE  FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);
CREATE POLICY irsyad_monitor_ro_org_restrictive ON public.sch_student_parents AS RESTRICTIVE FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);

DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.tabung_coin_deposits;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.tabung_coin_deposits;
CREATE POLICY irsyad_monitor_ro_org_permissive  ON public.tabung_coin_deposits AS PERMISSIVE  FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);
CREATE POLICY irsyad_monitor_ro_org_restrictive ON public.tabung_coin_deposits AS RESTRICTIVE FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);

DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.org_members;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.org_members;
CREATE POLICY irsyad_monitor_ro_org_permissive  ON public.org_members AS PERMISSIVE  FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);
CREATE POLICY irsyad_monitor_ro_org_restrictive ON public.org_members AS RESTRICTIVE FOR SELECT TO irsyad_monitor_ro
  USING (org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid);

-- ui_events has no org_id: scope by irsyad membership. NULL user_id never matches IN (...) (ruling Q3).
DROP POLICY IF EXISTS irsyad_monitor_ro_org_permissive  ON public.ui_events;
DROP POLICY IF EXISTS irsyad_monitor_ro_org_restrictive ON public.ui_events;
CREATE POLICY irsyad_monitor_ro_org_permissive  ON public.ui_events AS PERMISSIVE  FOR SELECT TO irsyad_monitor_ro
  USING (user_id IN (SELECT om.user_id FROM public.org_members om
                     WHERE om.org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid));
CREATE POLICY irsyad_monitor_ro_org_restrictive ON public.ui_events AS RESTRICTIVE FOR SELECT TO irsyad_monitor_ro
  USING (user_id IN (SELECT om.user_id FROM public.org_members om
                     WHERE om.org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid));

-- 6. Post-assert.
DO $post$
DECLARE
  c_tables constant text[] := ARRAY['audit_log','persons','sch_students','sch_student_parents',
                                    'tabung_coin_deposits','ui_events','org_members'];
  v_super  boolean;
  v_bypass boolean;
  v_inh    boolean;
  v_n      integer;
  v_list   text;
BEGIN
  SELECT rolsuper, rolbypassrls, rolinherit INTO v_super, v_bypass, v_inh
  FROM pg_roles WHERE rolname = 'irsyad_monitor_ro';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: role does not exist';
  END IF;
  IF v_super OR v_bypass OR v_inh THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: bad attributes rolsuper=% rolbypassrls=% rolinherit=% (all must be false)',
      v_super, v_bypass, v_inh;
  END IF;

  SELECT count(*) INTO v_n
  FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
  WHERE r.rolname = 'irsyad_monitor_ro';
  IF v_n <> 0 THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: role is a member of % role(s); must be 0', v_n;
  END IF;

  SELECT count(*) INTO v_n
  FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
  WHERE r.rolname = 'irsyad_monitor_ro'
    AND s.setdatabase = 0
    AND 'default_transaction_read_only=on' = ANY (s.setconfig);
  IF v_n <> 1 THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: default_transaction_read_only=on is not set on the role';
  END IF;

  -- The policy-support grants must have actually landed (a GRANT without grant option only WARNs).
  IF NOT has_schema_privilege('irsyad_monitor_ro', 'public', 'USAGE')
     OR NOT has_schema_privilege('irsyad_monitor_ro', 'auth', 'USAGE') THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: missing USAGE on schema public or auth';
  END IF;
  IF NOT has_function_privilege('irsyad_monitor_ro', 'public.auth_nonadmin_staff_student_person_ids()', 'EXECUTE')
     OR NOT has_function_privilege('irsyad_monitor_ro', 'public.auth_subadmin_student_person_ids()', 'EXECUTE')
     OR NOT has_function_privilege('irsyad_monitor_ro', 'public.auth_user_subadmin_org_ids()', 'EXECUTE')
     OR NOT has_function_privilege('irsyad_monitor_ro', 'auth.uid()', 'EXECUTE') THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: missing EXECUTE on a policy helper or auth.uid()';
  END IF;

  -- RLS must be ON for every granted table, or the policies are inert and the grant exposes the whole table.
  SELECT string_agg(c.relname, ',' ORDER BY c.relname) INTO v_list
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relname = ANY (c_tables) AND NOT c.relrowsecurity;
  IF v_list IS NOT NULL THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: RLS is not enabled on: %', v_list;
  END IF;

  -- SELECT on exactly the 7 tables, and no other public base table.
  SELECT count(*) INTO v_n
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relname = ANY (c_tables)
    AND has_table_privilege('irsyad_monitor_ro', c.oid, 'SELECT');
  IF v_n <> 7 THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: SELECT on % of the 7 granted tables (expected 7)', v_n;
  END IF;

  SELECT string_agg(c.relname, ',' ORDER BY c.relname) INTO v_list
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND NOT (c.relname = ANY (c_tables))
    AND has_table_privilege('irsyad_monitor_ro', c.oid, 'SELECT');
  IF v_list IS NOT NULL THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: SELECT on non-granted public tables: %', v_list;
  END IF;

  -- No write privilege anywhere in public (tables, partitions, views, matviews, foreign tables).
  SELECT string_agg(c.relname, ',' ORDER BY c.relname) INTO v_list
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
    AND has_table_privilege('irsyad_monitor_ro', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE');
  IF v_list IS NOT NULL THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: write privilege on public relations: %', v_list;
  END IF;

  -- The 14 policies: 7 PERMISSIVE + 7 RESTRICTIVE, SELECT-only, scoped to this role only.
  SELECT count(*) INTO v_n FROM pg_policies
  WHERE schemaname = 'public' AND tablename = ANY (c_tables)
    AND policyname = 'irsyad_monitor_ro_org_permissive'
    AND permissive = 'PERMISSIVE' AND cmd = 'SELECT' AND roles = ARRAY['irsyad_monitor_ro']::name[];
  IF v_n <> 7 THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: % of 7 permissive policies present', v_n;
  END IF;
  SELECT count(*) INTO v_n FROM pg_policies
  WHERE schemaname = 'public' AND tablename = ANY (c_tables)
    AND policyname = 'irsyad_monitor_ro_org_restrictive'
    AND permissive = 'RESTRICTIVE' AND cmd = 'SELECT' AND roles = ARRAY['irsyad_monitor_ro']::name[];
  IF v_n <> 7 THEN
    RAISE EXCEPTION 'irsyad_monitor_ro_post: % of 7 restrictive policies present', v_n;
  END IF;

  RAISE NOTICE 'irsyad_monitor_ro: role + 7 table grants + 3 helper grants + auth usage + 14 policies asserted';
END
$post$;
