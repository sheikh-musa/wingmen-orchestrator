-- 032_anon_write_revoke_nontelemetry.sql — CAI-RESP-512 item 3: close the latent
-- anon-write class the rls_grant_lint surfaced. anon holds EFFECTIVE INSERT/UPDATE/
-- DELETE on 77 public tables (relacl anon=arwdxtm), incl payments/donations/persons/
-- receipts/audit_log/ruling_audit_log. RLS denies these live today (anon sees 0 of N
-- rows on every sampled table — independently confirmed by cai), so this is
-- defense-in-depth, NOT a live hole. Removing the grant is provably a no-op on live
-- behavior (RLS was already the gate) — it just deletes the belt-and-suspenders risk
-- and turns the lint green.
--
-- EXACT SCOPE (CAI-RESP-512, binding — do not widen):
--   * REVOKE INSERT/UPDATE/DELETE from anon AND PUBLIC on ALL public tables.
--   * KEEP anon INSERT on ui_events ONLY (deliberate client telemetry ingest) — re-granted below.
--   * DO NOT touch `authenticated`: the 4 mizan tables (eval_runs/eval_set/human_reviews/
--     user_feedback) have legitimate authenticated write policies = the app's write path.
--     Revoking authenticated would break them.
--
-- Idempotent (REVOKE + GRANT). No DROP TABLE/COLUMN — passes check_additive_migration.py.
-- The file owns no BEGIN/COMMIT; the applier owns the txn so --dry-run can ROLLBACK.
-- Apply via scripts/apply_anon_revoke_032.py (decision-962: never `supabase db push`).
-- GATED (CAI-512): latent != live -> standard 24h window. DO NOT APPLY until the window
-- closes AND cai re-confirms the SQL matches the approved scope (filename+sha posted).

-- ── revoke anon + PUBLIC write on every public table ──
REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM anon, PUBLIC;

-- ── restore the ONE sanctioned exception: ui_events anon telemetry INSERT ──
GRANT INSERT ON public.ui_events TO anon;

-- ── assertion gate — verify intended effect, RAISE (rollback) if not ──
DO $gate$
DECLARE
  bad text;
BEGIN
  -- (1) no anon/PUBLIC INSERT/UPDATE/DELETE survives anywhere EXCEPT ui_events INSERT
  SELECT string_agg(table_name || '/' || grantee || '/' || privilege_type, ', ') INTO bad
  FROM information_schema.role_table_grants
  WHERE table_schema='public'
    AND grantee IN ('anon','PUBLIC')
    AND privilege_type IN ('INSERT','UPDATE','DELETE')
    AND NOT (table_name='ui_events' AND grantee='anon' AND privilege_type='INSERT');
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'unexpected residual anon/PUBLIC write grant(s): %', bad;
  END IF;

  -- (2) ui_events KEEPS anon INSERT (telemetry path intact)
  IF NOT has_table_privilege('anon', 'public.ui_events', 'INSERT') THEN
    RAISE EXCEPTION 'ui_events lost anon INSERT — telemetry path broken';
  END IF;

  -- (3) `authenticated` write is UNTOUCHED on the 4 mizan app-write tables
  FOR bad IN
    SELECT t FROM unnest(ARRAY['mizan_eval_runs','mizan_eval_set','mizan_human_reviews','mizan_user_feedback']) t
  LOOP
    IF NOT has_table_privilege('authenticated', 'public.'||bad, 'INSERT') THEN
      RAISE EXCEPTION 'authenticated lost INSERT on % — app write path broken', bad;
    END IF;
  END LOOP;
END
$gate$;

-- ── forensic ledger (populated statements) ──
INSERT INTO supabase_migrations.schema_migrations (version, name, statements)
VALUES ('20260722020000', '032_anon_write_revoke_nontelemetry', ARRAY[
  $stmt$REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM anon, PUBLIC$stmt$,
  $stmt$GRANT INSERT ON public.ui_events TO anon  -- keep telemetry ingest$stmt$
]::text[])
ON CONFLICT (version) DO NOTHING;
