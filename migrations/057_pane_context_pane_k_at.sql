-- 057: pane_context.pane_k_at — the "last observed NON-NULL pane_k" timestamp
-- (never-blank lane-context fix, Musa flag via Nazim #32472 / design #32484/#32489).
--
-- WHY: item-4a switched WORKER lane ctx% to PANE-TRUTH (pane_k, the `/clear to save
-- {N}k` hint). But that hint is HIDDEN for genuinely-low idle lanes AND for ANY lane
-- MID-TURN — so those lanes published a NULL pane_k and their card blanked to `—`,
-- while singletons (reading the always-populated gauge) kept showing a %. To NEVER
-- blank a worker card without re-introducing the stale-gauge lie, the publisher now
-- KEEPS the last non-null pane_k (COALESCE on upsert) and this column stamps WHEN that
-- reading was last freshly observed. The console shows: LIVE % when fresh, an
-- age-stamped LAST-KNOWN ("~{k}k · {age}") while within a bounded window, and an
-- idle/low/n-a label once it ages out — never a bare `—`, never the gauge number.
--
-- Pure OPS-CACHE additive column, no PII / money / governance / residency — same class
-- as 042/043 (console-signed schema add, not cai-gated). RLS/grants are UNCHANGED: a new
-- column inherits the table's row-level policies + the FOR-ALL/SELECT grants on
-- service_role/console_readonly, so no policy or GRANT edit is needed. Apply via direct
-- psycopg (decision-962: NEVER `supabase db push`).
-- Reversible: ALTER TABLE public.pane_context DROP COLUMN pane_k_at;
--
-- NOTE: no BEGIN/COMMIT here on purpose — the applier owns the transaction
-- (autocommit=False) so its dry-run TRULY rolls back (incl. the backfill below).

ALTER TABLE public.pane_context
  ADD COLUMN IF NOT EXISTS pane_k_at timestamptz;  -- when pane_k was last observed NON-NULL

-- Backfill: seed pane_k_at = updated_at for rows that ALREADY carry a pane_k reading,
-- so a lane with an existing last-known value gets an honest (row-fresh) age right away
-- instead of NULL. Idempotent (only touches rows still NULL).
UPDATE public.pane_context
   SET pane_k_at = updated_at
 WHERE pane_k IS NOT NULL AND pane_k_at IS NULL;
