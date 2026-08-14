-- 043: pane_context.pct — the CLIFF-truth column (op#13186, additive over 042).
-- 042's only bloat signal is pane_k (reclaimable K-tokens from CC's `/clear to save
-- {N}k` hint). But at very high context (>=~95%) Claude Code STOPS rendering that hint
-- and prints `{N}% context used` instead — so the MOST-bloated lane published a NULL
-- pane_k and read as not-bloated (verified 2026-08-14: cc-ihsanos-1 @100% invisible;
-- console showed 0 BLOAT while its own card said `100% context used`). This adds a
-- nullable `pct` (0-100) the publisher fills from CC's pct line; the console prefers it
-- (authoritative near the cliff) and falls back to pane_k below it. NULL = no pct signal
-- (below the cliff OR unreadable) => the console treats it as UNKNOWN, never a fake 0.
--
-- Pure OPS-CACHE additive column, no PII / money / governance / residency — same class
-- as 042 (console-signed schema add, not cai-gated). RLS/grants are unchanged: a new
-- column inherits the table's row-level policies + the FOR-ALL/SELECT grants 042 set on
-- service_role/console_readonly, so no policy or GRANT edit is needed (and none here
-- would narrow them). Apply via direct psycopg (decision-962: NEVER `supabase db push`).
-- Reversible: ALTER TABLE public.pane_context DROP COLUMN pct;

BEGIN;

ALTER TABLE public.pane_context
  ADD COLUMN IF NOT EXISTS pct smallint;  -- current-context percent (0-100) from CC's
                                          -- `{N}% context used` line; NULL = no pct signal

COMMIT;
