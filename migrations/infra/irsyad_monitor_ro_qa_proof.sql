-- irsyad_monitor_ro QA PROOF (irsyad-qa silo, ref oovskwogvefnznfowjky). NEVER run on goumlyne or ceayj.
-- Refs: console #81213 item 4; rulings #82720.
--
-- WHAT: one SQL file, run by the exec tool inside ONE transaction. It
--   (a) applies the REAL migration text (irsyad_monitor_ro_goumlyne.sql, included VERBATIM below
--       between the BEGIN-INCLUDE / END-INCLUDE markers; keep the two in sync: the orchestrator
--       test tests/test_irsyad_monitor_ro_sql.py fails if they drift),
--   (b) seeds a synthetic irsyad org (the real irsyad id, absent on QA) and a decoy org, with rows in
--       all 7 tables, including soft-deleted rows and a NULL-user ui_events row,
--   (c) switches to irsyad_monitor_ro and counts what it can see, plus the denial checks,
--   (d) reads the role attributes and memberships from the catalog,
--   (e) ends with RAISE EXCEPTION 'QA_PROOF_RESULT ...' carrying the full PASS/FAIL summary.
--       That error rolls back EVERYTHING (CREATE ROLE is transactional), so the proof leaves
--       nothing behind on QA. A run that ends any other way is itself a failure to investigate.
--
-- Output carries only ids-free counts and SQLSTATEs. All seeded text is synthetic.
-- No top-level BEGIN/COMMIT (the exec path wraps one transaction).

-- Guard FIRST, before anything is created: the real irsyad org must not exist here (QA has 7 other orgs, not irsyad).
DO $qa_guard$
BEGIN
  IF EXISTS (SELECT 1 FROM public.organizations WHERE id = '73339164-7c1f-40ba-a093-33f1f292dd4c'::uuid) THEN
    RAISE EXCEPTION 'QA_PROOF_RESULT ABORT: org 73339164 exists on this silo; this proof is QA-only. Nothing created.';
  END IF;
END
$qa_guard$;

-- ===== BEGIN-INCLUDE irsyad_monitor_ro_goumlyne.sql =====
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
-- ===== END-INCLUDE irsyad_monitor_ro_goumlyne.sql =====

DO $proof$
DECLARE
  c_irs     constant uuid   := '73339164-7c1f-40ba-a093-33f1f292dd4c';
  c_dec     constant uuid   := gen_random_uuid();
  c_tables  constant text[] := ARRAY['audit_log','persons','sch_students','sch_student_parents',
                                     'tabung_coin_deposits','ui_events','org_members'];
  c_helpers constant text[] := ARRAY['public.auth_nonadmin_staff_student_person_ids()',
                                     'public.auth_subadmin_student_person_ids()',
                                     'public.auth_user_subadmin_org_ids()'];
  c_nl      constant text   := chr(10);
  v_owner   name := current_user;
  v_out     text := '';
  v_fail    integer := 0;
  v_ids     jsonb := '{}'::jsonb;
  v_users   uuid[];
  v_umode   text := 'reused';
  v_org     uuid;
  v_tag     text;
  v_user    uuid;
  v_pa      uuid;
  v_pb      uuid;
  v_pc      uuid;
  v_s1      uuid;
  v_s2      uuid;
  v_id      text;
  v_ent     uuid;
  v_payload text;
  v_prev    text;
  v_amode   text;
  v_t       text;
  v_stmt    text;
  v_other   text;
  v_irs_ids text[];
  v_dec_ids text[];
  v_n       bigint;
  v_oi      bigint;
  v_od      bigint;
  v_vi      bigint;
  v_vd      bigint;
  v_vo      bigint;
  v_ok      boolean;
  v_bool    boolean;
  v_fn_den  integer := 0;
  v_super   boolean;
  v_bypass  boolean;
  v_inh     boolean;
  v_login   boolean;
  i         integer;
BEGIN
  -- Safety: the real irsyad org must NOT exist here. If it does, this is not the QA silo: stop.
  IF EXISTS (SELECT 1 FROM public.organizations WHERE id = c_irs) THEN
    RAISE EXCEPTION 'QA_PROOF_RESULT ABORT: org 73339164 already exists on this silo; this proof is QA-only. Nothing kept (rolled back).';
  END IF;

  -- ── (d) catalog: role attributes + memberships (read BEFORE the proof-only SET ROLE grant) ──
  BEGIN
    SELECT rolsuper, rolbypassrls, rolinherit, rolcanlogin INTO v_super, v_bypass, v_inh, v_login
    FROM pg_roles WHERE rolname = 'irsyad_monitor_ro';
    v_ok := FOUND AND NOT v_super AND NOT v_bypass AND NOT v_inh;
    IF v_ok THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
    v_out := v_out || ' d_role_attrs rolsuper=' || coalesce(v_super::text,'null') || ' rolbypassrls=' || coalesce(v_bypass::text,'null')
                   || ' rolinherit=' || coalesce(v_inh::text,'null') || ' rolcanlogin=' || coalesce(v_login::text,'null');
  EXCEPTION WHEN others THEN
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL d_role_attrs sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  BEGIN
    SELECT count(*) INTO v_n FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = 'irsyad_monitor_ro';
    SELECT count(*) INTO v_vo FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid WHERE r.rolname = 'irsyad_monitor_ro';
    IF v_n = 0 THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
    v_out := v_out || ' d_member_of_roles=' || v_n || ' (expect 0; also incl pg_read_all_data)'
                   || ' info_roles_holding_it=' || v_vo || ' (creator ADMIN grant on PG16+, not a privilege of the role)';
    SELECT count(*) INTO v_n FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid
    WHERE r.rolname = 'irsyad_monitor_ro' AND g.rolname = 'pg_read_all_data';
    IF v_n = 0 THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
    v_out := v_out || ' d_not_in_pg_read_all_data rows=' || v_n;
    SELECT count(*) INTO v_n FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
    WHERE r.rolname = 'irsyad_monitor_ro' AND s.setdatabase = 0 AND 'default_transaction_read_only=on' = ANY (s.setconfig);
    IF v_n = 1 THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
    v_out := v_out || ' d_default_read_only_setting rows=' || v_n;
  EXCEPTION WHEN others THEN
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL d_memberships sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  -- privilege sweep over every public relation (owner-side catalog read)
  BEGIN
    SELECT count(*) INTO v_vi FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relname = ANY (c_tables)
      AND has_table_privilege('irsyad_monitor_ro', c.oid, 'SELECT');
    SELECT count(*) INTO v_vd FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND NOT (c.relname = ANY (c_tables))
      AND has_table_privilege('irsyad_monitor_ro', c.oid, 'SELECT');
    SELECT count(*) INTO v_vo FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
      AND has_table_privilege('irsyad_monitor_ro', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE');
    SELECT count(*) INTO v_n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('v','m') AND has_table_privilege('irsyad_monitor_ro', c.oid, 'SELECT');
    IF v_vi = 7 AND v_vd = 0 AND v_vo = 0 THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
    v_out := v_out || ' d_privilege_sweep select_granted=' || v_vi || '/7 select_other_tables=' || v_vd
                   || ' write_any=' || v_vo || ' info_select_views=' || v_n;
  EXCEPTION WHEN others THEN
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL d_privilege_sweep sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  -- ── (b) seed: users ──
  BEGIN
    SELECT array_agg(s.id) INTO v_users
    FROM (SELECT p.id FROM public.profiles p JOIN auth.users u ON u.id = p.id ORDER BY p.created_at, p.id LIMIT 2) s;
    IF coalesce(array_length(v_users, 1), 0) < 2 THEN
      v_umode := 'synthetic';
      v_users := ARRAY[]::uuid[];
      FOR i IN 1..2 LOOP
        v_user := gen_random_uuid();
        INSERT INTO auth.users (id, aud, role, email)
        VALUES (v_user, 'authenticated', 'authenticated', 'qa-proof-' || i || '-' || left(v_user::text, 8) || '@example.invalid');
        INSERT INTO public.profiles (id, display_name) VALUES (v_user, 'QA Proof User ' || i) ON CONFLICT (id) DO NOTHING;
        v_users := v_users || v_user;
      END LOOP;
    END IF;
    v_out := v_out || c_nl || 'PASS b_seed_users mode=' || v_umode || ' n=' || array_length(v_users, 1);
  EXCEPTION WHEN others THEN
    v_users := NULL;
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_users sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  -- ── (b) seed: both orgs, all 7 tables ──
  FOR i IN 1..2 LOOP
    v_org  := CASE i WHEN 1 THEN c_irs ELSE c_dec END;
    v_tag  := CASE i WHEN 1 THEN 'irs' ELSE 'dec' END;
    v_user := v_users[i];
    v_pa := NULL; v_pb := NULL; v_pc := NULL; v_s1 := NULL; v_s2 := NULL;

    BEGIN
      INSERT INTO public.organizations (id, name, type) VALUES (v_org, 'QA Proof Org ' || upper(v_tag), 'mosque');
      v_out := v_out || c_nl || 'PASS b_seed_org_' || v_tag;
    EXCEPTION WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_org_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;

    BEGIN
      INSERT INTO public.persons (org_id, display_name) VALUES (v_org, 'QA Proof Person A') RETURNING id INTO v_pa;
      INSERT INTO public.persons (org_id, display_name, deleted_at) VALUES (v_org, 'QA Proof Person B', now()) RETURNING id INTO v_pb;
      INSERT INTO public.persons (org_id, display_name) VALUES (v_org, 'QA Proof Parent C') RETURNING id INTO v_pc;
      v_ids := v_ids || jsonb_build_object('persons_' || v_tag, jsonb_build_array(v_pa::text, v_pb::text, v_pc::text));
      v_out := v_out || c_nl || 'PASS b_seed_persons_' || v_tag || ' rows=3 soft_deleted=1';
    EXCEPTION WHEN others THEN
      v_pa := NULL; v_pb := NULL; v_pc := NULL;
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_persons_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;

    BEGIN
      INSERT INTO public.sch_students (org_id, person_id, student_number, status)
      VALUES (v_org, v_pa, 'QAP-' || v_tag || '-1', 'active') RETURNING id INTO v_s1;
      INSERT INTO public.sch_students (org_id, person_id, student_number, status, deleted_at)
      VALUES (v_org, v_pb, 'QAP-' || v_tag || '-2', 'active', now()) RETURNING id INTO v_s2;
      v_ids := v_ids || jsonb_build_object('sch_students_' || v_tag, jsonb_build_array(v_s1::text, v_s2::text));
      v_out := v_out || c_nl || 'PASS b_seed_sch_students_' || v_tag || ' rows=2 soft_deleted=1';
    EXCEPTION WHEN others THEN
      v_s1 := NULL; v_s2 := NULL;
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_sch_students_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;

    BEGIN
      INSERT INTO public.sch_student_parents (org_id, student_id, parent_person_id, relationship)
      VALUES (v_org, v_s1, v_pc, 'mother') RETURNING id::text INTO v_id;
      v_ids := v_ids || jsonb_build_object('sch_student_parents_' || v_tag, jsonb_build_array(v_id));
      v_out := v_out || c_nl || 'PASS b_seed_sch_student_parents_' || v_tag || ' rows=1';
    EXCEPTION WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_sch_student_parents_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;

    BEGIN
      v_ent := gen_random_uuid();
      BEGIN
        -- canonical append path (mig379 + the canonicalizer), exactly as mig469 calls it
        v_payload := public.audit_canonical_payload(jsonb_build_object('qa_proof', 'monitor_ro'));
        PERFORM public.append_audit_log_batch_secure(jsonb_build_array(jsonb_build_object(
          'orgId',       v_org::text,
          'actorId',     NULL::text,
          'entityType',  'qa_proof',
          'entityId',    v_ent::text,
          'action',      'create',
          'payloadJson', v_payload)));
        v_amode := 'canonical';
      EXCEPTION WHEN undefined_function THEN
        -- QA lacks the canonical appender: insert directly with the same hash rule
        -- (sha256(prev_hash || payload_json), genesis tip) so any chain check still holds.
        v_payload := '{"qa_proof":"monitor_ro"}';
        SELECT a.hash INTO v_prev FROM public.audit_log a WHERE a.org_id = v_org ORDER BY a.id DESC LIMIT 1;
        v_prev := coalesce(v_prev, 'genesis');
        INSERT INTO public.audit_log (org_id, actor_id, entity_type, entity_id, action, payload, prev_hash, hash)
        VALUES (v_org, NULL, 'qa_proof', v_ent, 'create', v_payload::jsonb, v_prev,
                encode(sha256(convert_to(v_prev || v_payload, 'UTF8')), 'hex'));
        v_amode := 'direct';
      END;
      v_ids := v_ids || jsonb_build_object('audit_log_' || v_tag,
        (SELECT coalesce(jsonb_agg(a.id::text), '[]'::jsonb) FROM public.audit_log a WHERE a.org_id = v_org AND a.entity_id = v_ent));
      v_out := v_out || c_nl || 'PASS b_seed_audit_log_' || v_tag || ' mode=' || v_amode
                     || ' rows=' || jsonb_array_length(v_ids -> ('audit_log_' || v_tag));
    EXCEPTION WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_audit_log_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;

    BEGIN
      INSERT INTO public.tabung_coin_deposits (org_id, period_start, period_end, notes)
      VALUES (v_org, current_date - 6, current_date, 'QA proof synthetic') RETURNING id::text INTO v_id;
      v_ids := v_ids || jsonb_build_object('tabung_coin_deposits_' || v_tag, jsonb_build_array(v_id));
      v_out := v_out || c_nl || 'PASS b_seed_tabung_coin_deposits_' || v_tag || ' rows=1';
    EXCEPTION WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_tabung_coin_deposits_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;

    BEGIN
      IF v_user IS NULL THEN
        RAISE EXCEPTION 'no seed user available' USING ERRCODE = 'P0001';
      END IF;
      INSERT INTO public.org_members (org_id, user_id, role) VALUES (v_org, v_user, 'viewer') RETURNING id::text INTO v_id;
      v_ids := v_ids || jsonb_build_object('org_members_' || v_tag, jsonb_build_array(v_id));
      v_out := v_out || c_nl || 'PASS b_seed_org_members_' || v_tag || ' rows=1';
    EXCEPTION WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_org_members_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;

    BEGIN
      IF v_user IS NULL THEN
        RAISE EXCEPTION 'no seed user available' USING ERRCODE = 'P0001';
      END IF;
      INSERT INTO public.ui_events (repo, user_id, event_name) VALUES ('qa-proof', v_user, 'qa_proof_event') RETURNING id::text INTO v_id;
      v_ids := v_ids || jsonb_build_object('ui_events_' || v_tag, jsonb_build_array(v_id));
      v_out := v_out || c_nl || 'PASS b_seed_ui_events_' || v_tag || ' rows=1';
    EXCEPTION WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_ui_events_' || v_tag || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;
  END LOOP;

  BEGIN
    INSERT INTO public.ui_events (repo, user_id, event_name) VALUES ('qa-proof', NULL, 'qa_proof_event') RETURNING id::text INTO v_id;
    v_ids := v_ids || jsonb_build_object('ui_events_null', jsonb_build_array(v_id));
    v_out := v_out || c_nl || 'PASS b_seed_ui_events_null_user rows=1';
  EXCEPTION WHEN others THEN
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL b_seed_ui_events_null_user sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  -- ── (c) prep: let the session user SET ROLE to the monitor role (proof-only; rolled back) ──
  BEGIN
    EXECUTE format('GRANT irsyad_monitor_ro TO %I', session_user);
    v_out := v_out || c_nl || 'INFO c_setup_set_role_grant ok';
  EXCEPTION WHEN others THEN
    v_out := v_out || c_nl || 'INFO c_setup_set_role_grant sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;
  -- The exec path owns actor identity and refuses SQL that touches the JWT claims, so auth.uid()
  -- here is the agent actor (a real member of one QA org). That makes the decoy check STRICTER
  -- (a real member's identity must still see no other org's rows); the helper sets reflect that
  -- actor's memberships, whereas a monitor connection with no claims gets empty sets.
  v_out := v_out || c_nl || 'INFO c_auth_uid_is_set ' || (auth.uid() IS NOT NULL)::text;

  -- ── (c) row visibility per table, as irsyad_monitor_ro ──
  FOREACH v_t IN ARRAY c_tables LOOP
    v_irs_ids := ARRAY(SELECT jsonb_array_elements_text(coalesce(v_ids -> (v_t || '_irs'), '[]'::jsonb)));
    v_dec_ids := ARRAY(SELECT jsonb_array_elements_text(coalesce(v_ids -> (v_t || '_dec'), '[]'::jsonb)));
    BEGIN
      EXECUTE format('SELECT count(*) FROM public.%I x WHERE x.id::text = ANY ($1)', v_t) INTO v_oi USING v_irs_ids;
      EXECUTE format('SELECT count(*) FROM public.%I x WHERE x.id::text = ANY ($1)', v_t) INTO v_od USING v_dec_ids;
      EXECUTE 'SET LOCAL ROLE irsyad_monitor_ro';
      EXECUTE format('SELECT count(*) FROM public.%I x WHERE x.id::text = ANY ($1)', v_t) INTO v_vi USING v_irs_ids;
      EXECUTE format('SELECT count(*) FROM public.%I x WHERE x.id::text = ANY ($1)', v_t) INTO v_vd USING v_dec_ids;
      IF v_t = 'ui_events' THEN
        EXECUTE 'SELECT count(*) FROM public.ui_events x WHERE x.user_id IS NULL' INTO v_vo;
      ELSE
        EXECUTE format('SELECT count(*) FROM public.%I x WHERE x.org_id IS DISTINCT FROM $1', v_t) INTO v_vo USING c_irs;
      END IF;
      EXECUTE format('SET LOCAL ROLE %I', v_owner);
      v_ok := v_oi > 0 AND v_vi = v_oi AND v_od > 0 AND v_vd = 0 AND v_vo = 0;
      IF v_ok THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
      v_out := v_out || ' c_rls_' || v_t || ' seeded_irs=' || v_oi || ' seen_irs=' || v_vi
                     || ' seeded_decoy=' || v_od || ' seen_decoy=' || v_vd
                     || CASE WHEN v_t = 'ui_events' THEN ' seen_null_user_silo_wide=' ELSE ' seen_other_org_silo_wide=' END || v_vo;
    EXCEPTION WHEN others THEN
      IF SQLSTATE = '42501' AND SQLERRM ILIKE '%function%' THEN v_fn_den := v_fn_den + 1; END IF;
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_rls_' || v_t || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;
  END LOOP;

  -- the seeded NULL-user ui_events row specifically
  BEGIN
    v_irs_ids := ARRAY(SELECT jsonb_array_elements_text(coalesce(v_ids -> 'ui_events_null', '[]'::jsonb)));
    EXECUTE 'SET LOCAL ROLE irsyad_monitor_ro';
    SELECT count(*) INTO v_vi FROM public.ui_events x WHERE x.id::text = ANY (v_irs_ids);
    EXECUTE format('SET LOCAL ROLE %I', v_owner);
    IF cardinality(v_irs_ids) = 1 AND v_vi = 0 THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
    v_out := v_out || ' c_ui_events_null_user seeded=' || cardinality(v_irs_ids) || ' seen=' || v_vi;
  EXCEPTION WHEN others THEN
    IF SQLSTATE = '42501' AND SQLERRM ILIKE '%function%' THEN v_fn_den := v_fn_den + 1; END IF;
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_ui_events_null_user sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  -- ── (c) information_schema column discovery: role sees every column the owner sees ──
  FOREACH v_t IN ARRAY ARRAY['persons','sch_students','sch_student_parents'] LOOP
    BEGIN
      SELECT count(*) INTO v_oi FROM information_schema.columns WHERE table_schema = 'public' AND table_name = v_t;
      EXECUTE 'SET LOCAL ROLE irsyad_monitor_ro';
      EXECUTE 'SELECT count(*) FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2'
        INTO v_vi USING 'public', v_t;
      EXECUTE format('SET LOCAL ROLE %I', v_owner);
      IF v_oi > 0 AND v_vi = v_oi THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
      v_out := v_out || ' c_info_schema_columns_' || v_t || ' owner=' || v_oi || ' role=' || v_vi;
    EXCEPTION WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_info_schema_columns_' || v_t || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;
  END LOOP;

  -- ── (c) auth.uid() is NULL for the role, and the 3 SECDEF helpers return EMPTY sets when called directly ──
  BEGIN
    EXECUTE 'SET LOCAL ROLE irsyad_monitor_ro';
    EXECUTE 'SELECT auth.uid() IS NULL' INTO v_bool;
    EXECUTE format('SET LOCAL ROLE %I', v_owner);
    IF v_bool THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
    v_out := v_out || ' c_auth_uid_is_null=' || v_bool;
  EXCEPTION WHEN others THEN
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_auth_uid sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  FOREACH v_t IN ARRAY c_helpers LOOP
    BEGIN
      EXECUTE 'SET LOCAL ROLE irsyad_monitor_ro';
      EXECUTE 'SELECT count(*) FROM ' || v_t INTO v_n;
      EXECUTE format('SET LOCAL ROLE %I', v_owner);
      IF v_n = 0 THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
      v_out := v_out || ' c_helper_empty ' || v_t || ' rows=' || v_n;
    EXCEPTION WHEN others THEN
      IF SQLSTATE = '42501' AND SQLERRM ILIKE '%function%' THEN v_fn_den := v_fn_den + 1; END IF;
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_helper_empty ' || v_t || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;
  END LOOP;

  -- ── (c) writes are refused (INSERT / UPDATE / DELETE on persons) ──
  v_irs_ids := ARRAY(SELECT jsonb_array_elements_text(coalesce(v_ids -> 'persons_irs', '[]'::jsonb)));
  FOREACH v_stmt IN ARRAY ARRAY[
      'INSERT INTO public.persons (org_id, display_name) VALUES (''73339164-7c1f-40ba-a093-33f1f292dd4c''::uuid, ''QA Proof Denied'')',
      'UPDATE public.persons SET is_active = is_active WHERE id::text = ANY ($1)',
      'DELETE FROM public.persons WHERE id::text = ANY ($1)'] LOOP
    v_t := split_part(v_stmt, ' ', 1);
    BEGIN
      EXECUTE 'SET LOCAL ROLE irsyad_monitor_ro';
      IF v_t = 'INSERT' THEN EXECUTE v_stmt; ELSE EXECUTE v_stmt USING v_irs_ids; END IF;
      EXECUTE format('SET LOCAL ROLE %I', v_owner);
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_write_refused_persons_' || lower(v_t) || ' statement succeeded';
    EXCEPTION
      WHEN insufficient_privilege THEN
        v_out := v_out || c_nl || 'PASS c_write_refused_persons_' || lower(v_t) || ' sqlstate=' || SQLSTATE;
      WHEN others THEN
        v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_write_refused_persons_' || lower(v_t) || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
    END;
  END LOOP;

  -- ── (c) a non-granted table is refused ──
  v_other := CASE WHEN to_regclass('public.donations') IS NOT NULL THEN 'donations' ELSE
    (SELECT c.relname::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT (c.relname = ANY (c_tables))
     ORDER BY c.relname LIMIT 1) END;
  BEGIN
    EXECUTE 'SET LOCAL ROLE irsyad_monitor_ro';
    EXECUTE format('SELECT count(*) FROM public.%I', v_other) INTO v_n;
    EXECUTE format('SET LOCAL ROLE %I', v_owner);
    v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_select_refused_' || coalesce(v_other, 'none') || ' select succeeded';
  EXCEPTION
    WHEN insufficient_privilege THEN
      v_out := v_out || c_nl || 'PASS c_select_refused_' || coalesce(v_other, 'none') || ' sqlstate=' || SQLSTATE;
    WHEN others THEN
      v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL c_select_refused_' || coalesce(v_other, 'none') || ' sqlstate=' || SQLSTATE || ' ' || left(SQLERRM, 160);
  END;

  -- ── (c) no "permission denied for function" anywhere on the 7 granted tables or the helpers ──
  IF v_fn_den = 0 THEN v_out := v_out || c_nl || 'PASS'; ELSE v_fail := v_fail + 1; v_out := v_out || c_nl || 'FAIL'; END IF;
  v_out := v_out || ' c_no_function_permission_denied hits=' || v_fn_den;

  -- ── (e) report and roll EVERYTHING back ──
  RAISE EXCEPTION 'QA_PROOF_RESULT %', 'overall=' || CASE WHEN v_fail = 0 THEN 'PASS' ELSE 'FAIL' END
    || ' fails=' || v_fail || v_out || c_nl || 'END (this error rolls back the role, policies, grants and all seed rows)';
END
$proof$;
