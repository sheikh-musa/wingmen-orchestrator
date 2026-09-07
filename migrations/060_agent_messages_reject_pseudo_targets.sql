-- 060_agent_messages_reject_pseudo_targets.sql
-- ledger: silo=tscuymavysscrvoberrr
--
-- Reject NEW agent_messages rows addressed to the retired pseudo-target
-- to_agent='substrate'. This is the STRUCTURAL belt behind cai's disposition
-- ruling: 'substrate' has no live reader — every historical row was bulk-
-- marked-read on a single sweep, never a live poll — so FYI echoes to it are
-- silently dropped (the daily dead-letter). cai #38077 (2026-09-07) ruled
-- RETIRE 'substrate' for real; orch-console reaped the 7 pre-fix rows and
-- confirmed scope (bus 38078/38080); the producer (cai) was already fixed
-- behaviour-level. This migration stops future misroutes from re-opening it.
--
-- SCOPE — substrate ONLY (deliberately narrow, gate-confirmed 38080):
--   * REJECT  'substrate'  — retired alias, no reader (cai #38077).
--   * KEEP    'broadcast'  — genuinely POLLED by every fleet body's standard
--                            drain; a real channel, NOT dead-lettered.
--   * KEEP    'musa'       — OPERATOR_AGENT (weekly_limit_monitor operator
--                            pages) + weekly_alert_relay cursor consumer. LIVE.
--   * KEEP    'operator'   — dormant (0 rows) BUT still read-side RECOGNIZED as
--                            an operator-facing channel (console/db.py:267,
--                            hosted_view.py:491 display to_agent IN
--                            ('musa','operator')). Retiring it touches operator-
--                            channel semantics = a SEPARATE cai ruling + read-
--                            side cleanup, NOT bundled here.
--   * KEEP    every real cc-* agent, including dead-but-registered lanes — they
--                            respawn. The reject set is the ruled-dead ALIAS
--                            only, never "no live body".
--
-- WHY `NOT VALID` IS MANDATORY: 76 historical 'substrate' rows exist. A
-- VALIDATED check would fail on those pre-existing rows and the whole apply
-- would roll back. NOT VALID enforces the constraint on NEW inserts/updates
-- only and leaves history intact (those rows are a settled audit record; cai
-- reaped the 7 recent FYI echoes, the older ones stay as history). No data is
-- mutated by this migration — it is a forward-only guard, trivially revertible.
--
-- REVERT: ALTER TABLE agent_messages DROP CONSTRAINT agent_messages_reject_pseudo_targets;
--
-- Constraint name is forward-looking: if a FUTURE cai ruling retires another
-- pseudo-target, this becomes CHECK (to_agent NOT IN ('substrate', ...)) in a
-- follow-up migration. Applied via scripts/apply_migration.py 060
-- --silo tscuymavysscrvoberrr (direct psycopg — NEVER `supabase db push`).

ALTER TABLE agent_messages
  ADD CONSTRAINT agent_messages_reject_pseudo_targets
  CHECK (to_agent <> 'substrate') NOT VALID;

-- In-txn post-apply self-check (fail LOUD, rolls the whole apply back on
-- failure): the constraint must exist AND be NOT VALID. A validated constraint
-- here would mean the 76 historical rows were (impossibly) re-checked, or the
-- guard silently landed in the wrong shape.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'agent_messages'::regclass
      AND conname  = 'agent_messages_reject_pseudo_targets'
      AND contype  = 'c'
      AND convalidated = false
  ) THEN
    RAISE EXCEPTION '060 post-apply FAILED: agent_messages_reject_pseudo_targets missing or unexpectedly VALIDATED (must be NOT VALID)';
  END IF;
END $$;
