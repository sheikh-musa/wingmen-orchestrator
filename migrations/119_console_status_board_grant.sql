-- 119_console_status_board_grant.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- GRANT for the "nothing goes dark" status view (bus 77019, Phase 1 —
-- /api/status-board + /status, console-revamp lane).
--
-- WHY: the status board unions the two anti-drop ledgers — open operator_asks
-- + open held_commitments — but console_readonly has never held SELECT on
-- held_commitments (it predates this read path; grants were added per-table
-- as surfaces appeared: 004/006/007/008/041/103/116/118). Without this grant
-- the new endpoint 500s with `permission denied for table
-- held_commitments` on every load — wet-proved live 2026-10-26 before this
-- migration existed.
--
-- SCOPE: SELECT only, table-level, one role. held_commitments rows carry
-- commitment text + channel tags (no PII columns; the operator-facing /asks
-- board already exposes the operator_asks analogue at the same auth tier).
-- Read path is nervous_system/console/db.py build_status_board_query(), the
-- same gated console deploy as every other surface.
--
-- Applied via scripts/apply_migration.py (direct psycopg, same-transaction
-- ledger write) — NEVER `supabase db push` (decision 962).

BEGIN;

SET LOCAL lock_timeout = '5s';

GRANT SELECT ON public.held_commitments TO console_readonly;

COMMIT;
