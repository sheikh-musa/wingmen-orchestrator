-- 081_substrate_april_residue_cleanup.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- CAI-RESP-1397 #5 post-apply assertions for the DROP FUNCTION statements
-- below (next_receipt_number/auth_user_hr_employee_id/auth_user_org_ids*/
-- sync_org_memberships_to_jwt, #45105):
-- assert: dropped public.next_receipt_number(uuid)
-- assert: dropped public.auth_user_hr_employee_id()
-- assert: dropped public.auth_user_org_ids()
-- assert: dropped public.auth_user_org_ids_fast()
-- assert: dropped public.auth_user_org_ids_with_role(text)
-- assert: dropped public.auth_user_org_ids_with_role_fast(text)
-- assert: dropped public.auth_user_org_ids_with_roles(text[])
-- assert: dropped public.auth_user_org_ids_with_roles_fast(text[])
-- assert: dropped public.sync_org_memberships_to_jwt()
--
-- Section A of the substrate April-residue cleanup, final consolidated form
-- (bus #45003/#45024/#45037/#45053/#45055/#45065/#45068/#45079/#45085/#45086/
-- #45098/#45101/#45104/#45105). Drops 14 orphaned tables from an abandoned
-- April ihsanos-era build (persons/donations/receipts scaffolding) and a
-- separate abandoned audit/anchoring build, both dead by every signal
-- checked: no FKs from outside the drop set, no views/matviews, no pg_cron
-- jobs, and -- critically, since pg_depend does not track plpgsql function
-- bodies -- no function anywhere in ANY schema (public/auth/storage/
-- extensions/cron/graphql/graphql_public/realtime/pgbouncer/vault, 359
-- functions swept) references any of these tables, except the handful of
-- functions this migration explicitly drops alongside them (see below).
--
-- KEEP, untouched, asserted to still exist after the drops: organizations,
-- payments, provisions, profiles, clients (the sites-vertical anchors; by
-- design per orch-console #45024).
--
-- RLS-DEPENDENCY FINDING (#45085, this session): org_members -- classified
-- dead by every FK/code-reference signal -- actually underpins 3 live-shaped
-- policies on KEEP tables (organizations x2, profiles x1), via both a direct
-- subquery on org_members and, less visibly, via auth_user_org_ids() (a
-- SECURITY DEFINER helper whose fallback branch queries org_members --
-- invisible to pg_depend, so it did NOT block a dry-run DROP TABLE, it would
-- have just started throwing "relation org_members does not exist" at
-- runtime). orch-console ruling (#45086/#45101/#45105): explicit named
-- DROP POLICY statements (no CASCADE) + explicit DROP FUNCTION for every
-- org_members-dependent helper, rather than leaving a landmine in place.
-- Dormancy verified (#45086/this session): last substrate auth.users
-- sign-in 2026-05-02T17:11:58Z, 21 total users, 0 since -- these policies
-- only ever served that dead April cohort; the live fleet reads this schema
-- via the service role (bypasses RLS entirely either way).
--
-- ALSO explicitly dropped (#45105), found by the same full sweep, both
-- confirmed via repo-wide grep to have zero live caller anywhere across
-- ~180 ihsanos/ihsanos-irsyad worktrees -- every real caller of these RPC
-- names targets that site's OWN Supabase project via createServerClient()
-- (ceayjeamtmcyzzvqflus / goumlynecruxrlmzlntp per LAYER-VOCAB-001), never
-- ORCHESTRATOR_SUPABASE_URL/tscuymavysscrvoberrr:
--   - next_receipt_number(uuid): receipts-only, no internal caller, but
--     EXECUTE granted to anon/authenticated -- RPC-reachable; dropping it
--     narrows exposure rather than leaving a receipts-shaped landmine.
--   - auth_user_hr_employee_id(): references persons (dropped below) via a
--     join to hr_employees, which does not exist in this DB at all -- this
--     function is already dead/broken today, independent of this migration;
--     dropped here so the post-drop "0 references" assertion doesn't have
--     to carry a pre-existing exception.
-- Checked and confirmed NOT related to this drop set (substring-match false
-- positives from the sweep, left untouched): ruling_audit_log (a live,
-- unrelated cai/governance hash-chain table -- "ruling_audit_log" contains
-- "audit_log" as a substring), cc_cai_audit_log, ecosystem_audit_log (same
-- substring-match artifact on unrelated sequence names).
--
-- FK-SAFE + POLICY-SAFE DROP ORDER:
--   1. receipts        -- FKs -> donations, persons, profiles, organizations,
--                          itself (void_receipt_id). Nothing FKs into it.
--   2. donations        -- FKs -> donation_categories, persons, profiles,
--                          organizations. receipts already gone.
--   3. audit_log        -- 191 rows, manifested below before the drop
--                          (ruling #45068).
--   4. person_roles          -- FKs -> persons, organizations.
--   5. person_relationships  -- FKs -> persons (x2), organizations.
--   6. org_role_permissions  -- FKs -> organizations.
--   7. donation_categories   -- FKs -> organizations. donations already gone.
--   8. persons          -- now nothing FKs into it.
--   9. organizations_fiscal_config -- FKs -> organizations only, independent
--                          cluster (checked #45053: zero functions/RLS/
--                          views reference it).
--  10. anchor_queue     -- FKs -> anchor_batches (batch_id). Must precede it.
--  11. anchor_batches   -- FKs -> organizations.
--  12. audit_portal_config -- FKs -> organizations, profiles. No incoming FK.
--  13. data_exports     -- FKs -> organizations (0 rows, added #45086).
--  -- explicit DROP POLICY x3 on the two KEEP tables whose policies
--     reference org_members, THEN:
--  14. org_members      -- FKs -> organizations, profiles. Dropped last so
--                          every other drop-set table's own org_members-
--                          referencing policies vanish with their own table
--                          first (no CASCADE needed for a table's own
--                          policies); only the 3 KEEP-table policies needed
--                          an explicit drop.
--  -- explicit DROP FUNCTION for next_receipt_number, auth_user_hr_employee_id,
--     the 6 auth_user_org_ids* signatures, and sync_org_memberships_to_jwt
--     (org_members's own trigger function; the trigger itself already went
--     with the table above -- a trigger cannot outlive DROP TABLE, but its
--     function is a separate object and needs its own explicit drop).
--
-- audit_log manifest (queried 2026-09-28, 191 rows total, recorded here per
-- orch-console #45068 so the record survives the drop):
--   by entity_type: donation=31, receipt=31, organization=5, hr_employee=8,
--     hr_payroll_run=1, pos_session=3, super_admin_access=88, sch_subject=24
--   by action:      create=186, update=5
--   by org_id (3 distinct):
--     40fbfeb2-c8b8-4cb9-9846-85543c88c39e = 95
--     b9d41941-a245-4434-b5f0-416d8250bdb0 = 8
--     00000000-0000-0000-0000-000000000000 (system/no-org) = 88
--   created_at range: 2026-04-04T17:29:39.781554+00:00 .. 2026-05-02T17:12:20.766111+00:00
--
-- REVERT: no revert. Dropped tables/functions are not recreated from this
-- migration -- restore from a pre-081 backup/snapshot of tscuymavysscrvoberrr
-- if ever needed. audit_log's content beyond the manifest above is not
-- recoverable after COMMIT.
--
-- HOLD (orch-console #45068/#45079/#45086/#45101/#45105): do not apply
-- before the 2026-09-28 credential rotation completes and Musa has cleared
-- Section A+B together.

BEGIN;

DO $$
DECLARE
  receipts_n bigint;
  donations_n bigint;
  audit_log_n bigint;
  person_roles_n bigint;
  person_relationships_n bigint;
  org_members_n bigint;
  org_role_permissions_n bigint;
  donation_categories_n bigint;
  persons_n bigint;
  fiscal_config_n bigint;
  anchor_queue_n bigint;
  anchor_batches_n bigint;
  audit_portal_config_n bigint;
  data_exports_n bigint;
  auth_dormant_ok boolean;
BEGIN
  SELECT count(*) INTO receipts_n FROM receipts;
  SELECT count(*) INTO donations_n FROM donations;
  SELECT count(*) INTO audit_log_n FROM audit_log;
  SELECT count(*) INTO person_roles_n FROM person_roles;
  SELECT count(*) INTO person_relationships_n FROM person_relationships;
  SELECT count(*) INTO org_members_n FROM org_members;
  SELECT count(*) INTO org_role_permissions_n FROM org_role_permissions;
  SELECT count(*) INTO donation_categories_n FROM donation_categories;
  SELECT count(*) INTO persons_n FROM persons;
  SELECT count(*) INTO fiscal_config_n FROM organizations_fiscal_config;
  SELECT count(*) INTO anchor_queue_n FROM anchor_queue;
  SELECT count(*) INTO anchor_batches_n FROM anchor_batches;
  SELECT count(*) INTO audit_portal_config_n FROM audit_portal_config;
  SELECT count(*) INTO data_exports_n FROM data_exports;

  IF NOT (
    receipts_n = 31 AND donations_n = 31 AND audit_log_n = 191
    AND person_roles_n = 59 AND person_relationships_n = 0
    AND org_members_n = 23 AND org_role_permissions_n = 112
    AND donation_categories_n = 26 AND persons_n = 58
    AND fiscal_config_n = 6
    AND anchor_queue_n = 0 AND anchor_batches_n = 0 AND audit_portal_config_n = 0
    AND data_exports_n = 0
  ) THEN
    RAISE EXCEPTION
      '081 PRECONDITION FAILED: row counts drifted from the verified set. Got receipts=%, donations=%, audit_log=%, person_roles=%, person_relationships=%, org_members=%, org_role_permissions=%, donation_categories=%, persons=%, organizations_fiscal_config=%, anchor_queue=%, anchor_batches=%, audit_portal_config=%, data_exports=% (expected 31/31/191/59/0/23/112/26/58/6/0/0/0/0). Aborting -- scope has drifted since this migration was written.',
      receipts_n, donations_n, audit_log_n, person_roles_n, person_relationships_n,
      org_members_n, org_role_permissions_n, donation_categories_n, persons_n,
      fiscal_config_n, anchor_queue_n, anchor_batches_n, audit_portal_config_n,
      data_exports_n;
  END IF;

  -- #45086: org_members-backed KEEP-table policies only ever served the dead
  -- April cohort. Guard that no one has signed in since -- if someone has,
  -- the dormancy premise the whole ruling rests on no longer holds.
  SELECT (max(last_sign_in_at) IS NULL OR max(last_sign_in_at) < '2026-06-01'::timestamptz)
    INTO auth_dormant_ok
  FROM auth.users;

  IF NOT auth_dormant_ok THEN
    RAISE EXCEPTION '081 PRECONDITION FAILED: auth.users has a sign-in on/after 2026-06-01 -- the dormancy premise behind dropping org_members/its RLS policies no longer holds. Aborting.';
  END IF;
END $$;

DROP TABLE receipts;
DROP TABLE donations;
DROP TABLE audit_log;
DROP TABLE person_roles;
DROP TABLE person_relationships;
DROP TABLE org_role_permissions;
DROP TABLE donation_categories;
DROP TABLE persons;
DROP TABLE organizations_fiscal_config;
DROP TABLE anchor_queue;
DROP TABLE anchor_batches;
DROP TABLE audit_portal_config;
DROP TABLE data_exports;

-- Explicit, named, no CASCADE (#45086/#45105) -- the only 3 policies anywhere
-- outside the drop set that reference org_members (confirmed by this
-- session's pg_policies + function-body sweep, #45104).
DROP POLICY "Org admins can update their own org" ON organizations;
DROP POLICY "Users can see their orgs" ON organizations;
DROP POLICY "Users can read profiles of org members" ON profiles;

DROP TABLE org_members;

-- #45105: dead/RPC-exposed helpers referencing now-dropped tables, confirmed
-- via repo-wide grep to have no live caller against this substrate project.
DROP FUNCTION next_receipt_number(uuid);
DROP FUNCTION auth_user_hr_employee_id();
DROP FUNCTION auth_user_org_ids();
DROP FUNCTION auth_user_org_ids_fast();
DROP FUNCTION auth_user_org_ids_with_role(text);
DROP FUNCTION auth_user_org_ids_with_role_fast(text);
DROP FUNCTION auth_user_org_ids_with_roles(text[]);
DROP FUNCTION auth_user_org_ids_with_roles_fast(text[]);
DROP FUNCTION sync_org_memberships_to_jwt();

DO $$
DECLARE
  leftover_policy_count int;
  leftover_fn_count int;
BEGIN
  IF to_regclass('public.organizations') IS NULL
     OR to_regclass('public.payments') IS NULL
     OR to_regclass('public.provisions') IS NULL
     OR to_regclass('public.profiles') IS NULL
     OR to_regclass('public.clients') IS NULL THEN
    RAISE EXCEPTION '081 POST-DROP FAILED: one of the KEEP tables (organizations/payments/provisions/profiles/clients) no longer exists. Aborting.';
  END IF;

  IF NOT (
    SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'organizations'
  ) OR NOT (
    SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'profiles'
  ) THEN
    RAISE EXCEPTION '081 POST-DROP FAILED: RLS is no longer enabled on organizations or profiles. Aborting.';
  END IF;

  -- No policy anywhere in public should still mention org_members or any of
  -- the other 13 dropped tables (the same regex the pre-write sweep used).
  SELECT count(*) INTO leftover_policy_count
  FROM pg_policies
  WHERE schemaname = 'public'
    AND (
      coalesce(qual, '') || ' ' || coalesce(with_check, '')
    ) ~ '\y(receipts|donations|audit_log|person_roles|person_relationships|org_members|org_role_permissions|donation_categories|persons|organizations_fiscal_config|anchor_queue|anchor_batches|audit_portal_config|data_exports)\y';

  IF leftover_policy_count > 0 THEN
    RAISE EXCEPTION '081 POST-DROP FAILED: % policy/policies still textually reference a dropped table. Aborting.', leftover_policy_count;
  END IF;

  -- No function anywhere in public should still reference a dropped table
  -- or a dropped helper function by name.
  SELECT count(*) INTO leftover_fn_count
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'public'
    AND p.prosrc ~ '\y(receipts|donations|audit_log|person_roles|person_relationships|org_members|org_role_permissions|donation_categories|persons|organizations_fiscal_config|anchor_queue|anchor_batches|audit_portal_config|data_exports|next_receipt_number|auth_user_hr_employee_id|auth_user_org_ids|sync_org_memberships_to_jwt)\y'
    AND p.proname NOT IN ('ruling_audit_log_fill_chain');  -- confirmed false positive (#45104): substring match on an unrelated live table's name, not a real reference to the dropped audit_log.

  IF leftover_fn_count > 0 THEN
    RAISE EXCEPTION '081 POST-DROP FAILED: % function(s) still reference a dropped table/function by name. Aborting.', leftover_fn_count;
  END IF;
END $$;

COMMIT;
