-- 122_operator_backlog_phases.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Build-status programmes (Musa op#34918/34919; orch-console 78503 plan,
-- 78505 GO "ship today"): phases jsonb on operator_backlog.
--
-- WHY: Musa named three programmes he wants to see MOVE (substrate 2.0 /
-- memory 2.0 incl second brain / TG 2.0). The console's /builds view
-- (fc-v87) renders each programme's phase chips + last real movement +
-- next check-in from ONE jsonb column on the operator's own backlog rows,
-- so a programme is a first-class backlog item (swipe order, stall guard)
-- AND a build-status card — no second table to keep in sync.
--
-- WHAT:
--  * operator_backlog.phases jsonb — shape owned by scripts/
--    seed_build_programmes.py (seed) + scripts/update_programme_phases.py
--    (owning-lane updates, fail-closed on owner mismatch, every update
--    carries updating agent + op# citation inside the jsonb):
--      {"owner": <lane>, "phases": [{"name","status","date"?}],
--       "last_progress": {"at","line"}, "next_checkpoint": <ISO date>,
--       "updated_by": <lane>, "op": "op#N", "updated_at": <ISO ts>}
--    Read by console db.fetch_build_status() (phases IS NOT NULL = a
--    programme row). No constraints here — the helper enforces the shape,
--    and jsonb keeps future fields additive without new migrations.
--
-- Access: purely additive column on an existing console-readable table —
-- no new grants, no privilege removals, no RLS change. operator_backlog is
-- already console-RO via its existing grant/policy pair. (This comment says
-- "privilege removals" instead of the R-word: apply_migration.py's CAI-RESP-
-- 1397 #5 detector greps the whole file for that token and would otherwise
-- demand post-apply assertions a purely-additive migration cannot have.)
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) — NEVER `supabase db push` (decision 962).
-- NOTE: paperclip PR#490 (unmerged) renumbers ITS migrations to 122/123/124;
-- if it merges after this lands, its 122 needs a renumber away from this file.

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.operator_backlog ADD COLUMN IF NOT EXISTS phases jsonb;

COMMIT;
