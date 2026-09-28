-- 081_substrate_april_residue_cleanup.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Section A of the substrate April-residue cleanup (bus #45003/#45037/#45053/
-- #45055/#45065, rulings finalized in orch-console #45068). Drops 4 orphaned
-- tables left over from an abandoned April audit/anchoring build: none has a
-- live code reference (verified by grepping ~190 repos under ~/wingmen/projects
-- plus this repo for each table name), and none holds data past a dead-era
-- dev/demo window.
--
-- FK-SAFE DROP ORDER (verified via information_schema): anchor_queue.batch_id
-- FKs to anchor_batches.id, so anchor_queue must drop first. audit_portal_config
-- and audit_log both FK out to organizations/profiles but nothing FKs into
-- either, so their order relative to the anchor_* pair doesn't matter.
--
-- 1. anchor_queue (0 rows) -- anchor-batch retry/backoff queue for a Solana
--    anchoring pipeline that was never wired to anything live. Confirmed:
--    scripts/queue_age_watchdog.py explicitly excluded it from scope (never
--    scanned it); migrations/013_substrate_rls_grant_lockdown.sql's RLS grants
--    for it are historical blanket hardening, not a live dependency. Its one
--    code mention (the watchdog's out-of-scope comment) is updated in this same
--    PR so no dangling reference survives the drop.
-- 2. anchor_batches (0 rows) -- the batches anchor_queue would have retried.
-- 3. audit_portal_config (0 rows) -- per-org display toggles for a public audit
--    portal that was never built.
-- 4. audit_log (191 rows) -- a hash-chained audit trail for the same abandoned
--    build. Orch-console ruling (#45068): DROP it whole -- the 5 'organization'
--    rows audit dev-era creates/updates in an orphaned log with no code
--    reference, and the live `organizations` rows (kept) don't depend on it.
--    Per the same ruling, a metadata-only manifest of what existed is recorded
--    here before the drop so the record survives the table:
--
--    audit_log manifest (queried 2026-09-28, 191 rows total):
--      by entity_type: donation=31, receipt=31, organization=5, hr_employee=8,
--        hr_payroll_run=1, pos_session=3, super_admin_access=88, sch_subject=24
--      by action:      create=186, update=5
--      by org_id (3 distinct):
--        40fbfeb2-c8b8-4cb9-9846-85543c88c39e = 95
--        b9d41941-a245-4434-b5f0-416d8250bdb0 = 8
--        00000000-0000-0000-0000-000000000000 (system/no-org) = 88
--      created_at range: 2026-04-04T17:29:39.781554+00:00 .. 2026-05-02T17:12:20.766111+00:00
--
-- REVERT: no revert. Dropped tables are not recreated from this migration --
-- restore from a pre-081 backup/snapshot of tscuymavysscrvoberrr if ever needed.
-- audit_log's content beyond this manifest is not recoverable after COMMIT.
--
-- HOLD (orch-console #45068): do not apply before the 2026-09-28 credential
-- rotation completes and Musa has cleared Section A+B together.

BEGIN;

DO $$
DECLARE
  aq_count bigint;
  ab_count bigint;
  apc_count bigint;
  al_count bigint;
BEGIN
  SELECT count(*) INTO aq_count FROM anchor_queue;
  SELECT count(*) INTO ab_count FROM anchor_batches;
  SELECT count(*) INTO apc_count FROM audit_portal_config;
  SELECT count(*) INTO al_count FROM audit_log;

  IF aq_count <> 0 OR ab_count <> 0 OR apc_count <> 0 THEN
    RAISE EXCEPTION
      '081 PRECONDITION FAILED: expected anchor_queue/anchor_batches/audit_portal_config all empty, got %/%/%. Aborting -- scope has drifted since this migration was written.',
      aq_count, ab_count, apc_count;
  END IF;

  IF al_count <> 191 THEN
    RAISE EXCEPTION
      '081 PRECONDITION FAILED: expected audit_log to have exactly 191 rows (the manifested set), got %. Aborting -- scope has drifted since this migration was written.',
      al_count;
  END IF;
END $$;

DROP TABLE anchor_queue;
DROP TABLE anchor_batches;
DROP TABLE audit_portal_config;
DROP TABLE audit_log;

COMMIT;
