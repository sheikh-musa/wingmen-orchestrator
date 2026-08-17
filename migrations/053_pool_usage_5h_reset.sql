-- 053_pool_usage_5h_reset.sql
-- Stop throwing away the 5-hour reset time we already fetch.
--
-- orch-console, 2026-08-17 — promised to the operator at op#13802.
--
-- THE DEFECT: `weekly_limit_monitor.probe_pool` already reads
--   `anthropic-ratelimit-unified-5h-reset` into p["reset5h"] and then DISCARDS it.
--   Only the WEEKLY reset was ever persisted (`pool_usage.resets_at`) or reported. So
--   every alert said "5h at 94%" and gave the reader no way to know whether that meant
--   twenty minutes of pain or four hours.
--
-- WHY IT MATTERED IN PRACTICE, and why it is worth a column rather than a one-line
--   format fix: on 2026-08-17 musa2's FIVE-HOUR window bound at 100% and stalled every
--   irsyad lane. The weekly figure was a comfortable 76% throughout — the binding
--   constraint was the one we were not storing. The operator asked "when does musa2's
--   5 hour window reset" and I had to hand-probe the live API to answer a question our
--   own alert had raised four minutes earlier. Anything that wants to reason about the
--   ACTUAL binding window — the console, a pacing decision, a client-facing "back at
--   12:10" — needs this persisted, not re-derived per caller.
--
-- Additive and reversible: one nullable column on each table, populated by the
-- monitor's next poll. Nothing reads it yet, so nothing breaks if it stays NULL.

BEGIN;

ALTER TABLE pool_usage
    ADD COLUMN IF NOT EXISTS resets_5h_at timestamptz;

ALTER TABLE pool_usage_history
    ADD COLUMN IF NOT EXISTS resets_5h_at timestamptz;

COMMENT ON COLUMN pool_usage.resets_5h_at IS
    'When the ROLLING 5-hour window tops up (anthropic-ratelimit-unified-5h-reset). '
    'Distinct from resets_at, which is the WEEKLY window. The 5h window is frequently '
    'the binding constraint while the weekly figure still looks comfortable — that is '
    'exactly how musa2 stalled the irsyad fleet on 2026-08-17 at 76% weekly.';

COMMENT ON COLUMN pool_usage_history.resets_5h_at IS
    'Per-poll snapshot of the 5-hour window reset; see pool_usage.resets_5h_at.';

COMMIT;
