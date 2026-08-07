-- 031_anon_write_truncate_lockdown.sql — CAI-RESP-511: formalize cai's two live
-- emergency fixes into the migration ledger (audit-trail gap: cai's direct
-- REVOKE/ALTER are live-correct but not yet reproducible from git), and add the
-- default-privileges hardening cai assigned so NEW tables never inherit the same
-- hole.
--
-- Context: migration 030 closed the 3 named RLS-off tables. Re-verifying the CLASS
-- surfaced two P0 holes RLS-on was HIDING (verified live via SET ROLE anon):
--   A) TRUNCATE is RLS-immune — Postgres never applies RLS to TRUNCATE, only the
--      table GRANT gates it. 77 public tables (incl. donations/payments/receipts/
--      persons/audit_log) granted anon/authenticated/PUBLIC TRUNCATE. anon could
--      wipe money+audit tables despite relrowsecurity=true.
--   B) 5 tables (clients, payments, chat_history, pending_signups, site_templates)
--      had a policy MISNAMED 'service role full access' but written FOR ALL TO
--      public USING(true) WITH CHECK(true) = every op to anon (anon read/updated
--      all 8 clients incl. email; read 157 chat_history rows; a MONEY-1 violation
--      on payments). cai ALTERed each to USING/WITH CHECK (auth.role()='service_role').
--
-- This migration is HARDENING ONLY + IDEMPOTENT (REVOKE is idempotent; DROP POLICY
-- IF EXISTS + CREATE reproduces the fixed state on a fresh rebuild). No DROP
-- TABLE/COLUMN, so it passes scripts/check_additive_migration.py. The file owns no
-- BEGIN/COMMIT; the applier owns the txn so --dry-run can ROLLBACK.
-- Apply via scripts/apply_anon_lockdown_031.py (decision-962: never `supabase db push`).
-- Intent call (CAI-RESP-511, Nazim): the 4 anon-SELECT tables cai left for review
-- (ruling_audit_log, audit_key_registry, daily_attestations, ingestion_provenance)
-- are cryptographic-transparency surfaces with ZERO PII — anon READ is deliberate
-- and RETAINED; only anon WRITE/TRUNCATE is forbidden (enforced by A + the lint).

-- ── A) TRUNCATE is RLS-immune: revoke it from every public caller, all tables ──
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM anon, authenticated, PUBLIC;

-- ── B) reproduce cai's 5 policy fixes (mis-named 'service role full access') ──
-- DROP+CREATE (not ALTER) so a fresh rebuild that starts from the original
-- USING(true) definition still lands on the service-role-only state.
DROP POLICY IF EXISTS "service role full access" ON public."clients";
CREATE POLICY "service role full access" ON public."clients"          FOR ALL TO public USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS "service role full access" ON public."payments";
CREATE POLICY "service role full access" ON public."payments"         FOR ALL TO public USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS "service role full access" ON public."chat_history";
CREATE POLICY "service role full access" ON public."chat_history"     FOR ALL TO public USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS "service role full access" ON public."pending_signups";
CREATE POLICY "service role full access" ON public."pending_signups"  FOR ALL TO public USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
DROP POLICY IF EXISTS "service role full access" ON public."site_templates";
CREATE POLICY "service role full access" ON public."site_templates"   FOR ALL TO public USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

-- ── C) DEFAULT PRIVILEGES: NEW tables must not inherit anon write / any-role TRUNCATE ──
-- Scope: the postgres-owned default ACL — the role the migration pipeline (and every
-- fleet script on DATABASE_URL) creates tables under, i.e. OUR real table-creation
-- vector. anon loses INSERT/UPDATE/DELETE/TRUNCATE by default (anon SELECT default
-- kept, per CAI-RESP-511 scope = write/TRUNCATE only; RLS still default-denies unread
-- tables). authenticated keeps INSERT/UPDATE/DELETE (the app's RLS-scoped write path)
-- but loses TRUNCATE. PUBLIC loses TRUNCATE. New tables needing anon writes (e.g.
-- telemetry) must GRANT explicitly — deny-by-default, the correct posture.
--
-- RESIDUAL (flagged by scripts/rls_grant_lint.py, NOT fixable here): a SECOND default
-- ACL owned by `supabase_admin` also grants anon write on new public tables. It governs
-- tables created via the Supabase dashboard / platform internals, and cannot be altered
-- by a `postgres` session (permission denied to SET ROLE supabase_admin). Closing it
-- needs a supabase_admin session / dashboard action — an operator+infra step, surfaced
-- to cai, not silently skipped.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE TRUNCATE ON TABLES FROM authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE TRUNCATE ON TABLES FROM PUBLIC;

-- ── Assertion gate — verify intended effect, RAISE (rollback) if not ──
DO $gate$
DECLARE
  bad text;
BEGIN
  -- (1) no anon/authenticated/PUBLIC TRUNCATE grant survives on ANY public table
  SELECT string_agg(DISTINCT grantee, ', ') INTO bad
  FROM information_schema.role_table_grants
  WHERE table_schema='public'
    AND grantee IN ('anon','authenticated','PUBLIC')
    AND privilege_type = 'TRUNCATE';
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'anon/auth/PUBLIC TRUNCATE grant still present for: %', bad;
  END IF;

  -- (2) the 5 formerly-public-write policies now gate on service_role in BOTH
  --     qual and with_check (no residual USING(true)/WITH CHECK(true))
  SELECT string_agg(tablename, ', ') INTO bad
  FROM pg_policies
  WHERE schemaname='public'
    AND tablename IN ('clients','payments','chat_history','pending_signups','site_templates')
    AND policyname='service role full access'
    AND (qual IS DISTINCT FROM '(auth.role() = ''service_role''::text)'
         OR with_check IS DISTINCT FROM '(auth.role() = ''service_role''::text)');
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'policy still not service-role-gated on: %', bad;
  END IF;

  -- (3) the POSTGRES-owned default ACL (our migration/script table-creation vector)
  --     no longer hands anon INSERT/UPDATE/DELETE/TRUNCATE to new tables. The
  --     supabase_admin-owned default is a platform residual out of SQL reach (see
  --     note above) — asserted by the lint, not here.
  IF EXISTS (
    SELECT 1
    FROM pg_default_acl d
    CROSS JOIN LATERAL aclexplode(d.defaclacl) a
    WHERE d.defaclnamespace = 'public'::regnamespace
      AND d.defaclobjtype = 'r'
      AND d.defaclrole = 'postgres'::regrole
      AND a.grantee = 'anon'::regrole
      AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')
  ) THEN
    RAISE EXCEPTION 'postgres-owned default privileges still grant anon write/TRUNCATE on new tables';
  END IF;
END
$gate$;

-- ── forensic ledger (populated statements) ──
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260722010000', '031_anon_write_truncate_lockdown', ARRAY[
  $stmt$REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM anon, authenticated, PUBLIC$stmt$,
  $stmt$DROP+CREATE POLICY "service role full access" -> auth.role()='service_role' on clients, payments, chat_history, pending_signups, site_templates$stmt$,
  $stmt$ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON TABLES FROM anon$stmt$,
  $stmt$ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE TRUNCATE ON TABLES FROM authenticated, PUBLIC$stmt$
]::text[])
ON CONFLICT (version) DO NOTHING;
