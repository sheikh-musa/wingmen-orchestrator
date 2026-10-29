-- irsyad_monitor_ro STEP 4 HARD-PASS read-back (console #82720/#82789). goumlyne only.
-- Run CONNECTED AS irsyad_monitor_ro over its real pooler DSN (no JWT claims), e.g.
--   psql "<irsyad_monitor_ro DSN>" -v ON_ERROR_STOP=0 -tA -f irsyad_monitor_ro_prod_readback.sql
-- Read-only (the role's default_transaction_read_only=on). Prints counts only, never row data.
-- PASS needs ALL of:
--   R1 current_user = irsyad_monitor_ro and auth.uid() is NOT nameable (expect ERROR 42501 on R1b)
--   R2 the 3 SECDEF helpers return 0 rows each
--   R3 per table: rows visible from orgs other than irsyad = 0 (ui_events: rows with NULL or non-irsyad user = 0)
--   R4 per table: irsyad rows visible (compare with the owner-side counts coord reads via exec_prod)
--   R5 information_schema column counts for persons / sch_students / sch_student_parents (compare with owner-side)
--   R6 an INSERT is refused (expect ERROR on R6)
-- Any FAIL = STOP, go to STEP R, report to console before any retry.

SELECT 'R1a current_user=' || current_user;
SELECT 'R1b auth_uid=' || coalesce(auth.uid()::text, 'NULL');

SELECT 'R2 auth_nonadmin_staff_student_person_ids rows=' || count(*) FROM public.auth_nonadmin_staff_student_person_ids();
SELECT 'R2 auth_subadmin_student_person_ids rows=' || count(*) FROM public.auth_subadmin_student_person_ids();
SELECT 'R2 auth_user_subadmin_org_ids rows=' || count(*) FROM public.auth_user_subadmin_org_ids();

SELECT 'R3 audit_log other_org=' || count(*) FILTER (WHERE org_id <> '73339164-7c1f-40ba-a093-33f1f292dd4c') || ' R4 irsyad=' || count(*) FILTER (WHERE org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c') FROM public.audit_log;
SELECT 'R3 persons other_org=' || count(*) FILTER (WHERE org_id <> '73339164-7c1f-40ba-a093-33f1f292dd4c') || ' R4 irsyad=' || count(*) FILTER (WHERE org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c') FROM public.persons;
SELECT 'R3 sch_students other_org=' || count(*) FILTER (WHERE org_id <> '73339164-7c1f-40ba-a093-33f1f292dd4c') || ' R4 irsyad=' || count(*) FILTER (WHERE org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c') FROM public.sch_students;
SELECT 'R3 sch_student_parents other_org=' || count(*) FILTER (WHERE org_id <> '73339164-7c1f-40ba-a093-33f1f292dd4c') || ' R4 irsyad=' || count(*) FILTER (WHERE org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c') FROM public.sch_student_parents;
SELECT 'R3 tabung_coin_deposits other_org=' || count(*) FILTER (WHERE org_id <> '73339164-7c1f-40ba-a093-33f1f292dd4c') || ' R4 irsyad=' || count(*) FILTER (WHERE org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c') FROM public.tabung_coin_deposits;
SELECT 'R3 org_members other_org=' || count(*) FILTER (WHERE org_id <> '73339164-7c1f-40ba-a093-33f1f292dd4c') || ' R4 irsyad=' || count(*) FILTER (WHERE org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c') FROM public.org_members;
SELECT 'R3 ui_events null_or_other_user=' || count(*) FILTER (WHERE user_id IS NULL OR user_id NOT IN (SELECT om.user_id FROM public.org_members om WHERE om.org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c')) || ' R4 irsyad=' || count(*) FILTER (WHERE user_id IN (SELECT om.user_id FROM public.org_members om WHERE om.org_id = '73339164-7c1f-40ba-a093-33f1f292dd4c')) FROM public.ui_events;

SELECT 'R5 columns ' || table_name || '=' || count(*) FROM information_schema.columns WHERE table_schema = 'public' AND table_name IN ('persons', 'sch_students', 'sch_student_parents') GROUP BY table_name ORDER BY table_name;

SELECT 'R6 insert attempt follows (expect ERROR)';
INSERT INTO public.persons (org_id, display_name) VALUES ('73339164-7c1f-40ba-a093-33f1f292dd4c', 'readback-must-fail');
