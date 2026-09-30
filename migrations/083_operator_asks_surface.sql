-- 083_operator_asks_surface.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Adds ask_surface, the axis that keeps Musa's "Your asks" board scoped to
-- HIS asks only, and closes a dedupe hole -- Musa op#23554 (bus #46353) +
-- cc-fleet-health op#23531 (bus #46360), agreed 2026-09-30.
--
-- PROBLEM 1 (delegation-as-ask, Musa op#23554): scripts/bus_send.py's
-- is_new_ask heuristic and scripts/console_assign.py's assign() both opened a
-- FRESH operator_asks row for every orch-console decision/assign to a lane,
-- even a pure fleet delegation with no source_msg_id -- i.e. nothing that
-- traces back to something Musa actually said. Confirmed live: ids 378
-- ("OEH: cc-quality review...") and 381 ("Musa picked V2 option B...") were
-- both console-to-lane delegations, source_msg_id NULL, phantom-appearing on
-- Musa's board as if he'd asked them himself. Fixed in code (this same PR):
-- neither writer opens a NEW row without a source_msg_id any more -- a
-- delegation with no traceable origin now touches operator_asks not at all;
-- one WITH a source_msg_id links (see PROBLEM 2) instead of blindly
-- inserting.
--
-- PROBLEM 2 (duplicates, Musa op#23554 item 2): the same delegation call
-- shape, repeated (e.g. broadcasting one hard rule to 3 bodies) produced 3
-- IDENTICAL rows with source_msg_id NULL (ids 210/211/212, exact same `ask`
-- text) -- PROBLEM 1's fix removes this specific case (no source_msg_id ->
-- no row at all), but a real dedupe guard is still needed for the
-- source_msg_id-tracked path: console_assign.py can legitimately be called
-- several times for ONE Musa message when it fans out into several DISTINCT
-- delegated sub-asks (confirmed live and must keep working: ids 18/19/20,
-- one source_msg_id, three different `ask` texts, three different
-- delegated_to). The actual duplicate shape is the SAME (source_msg_id, ask)
-- pair inserted twice (an accidental retry/double-post), not merely a
-- repeated source_msg_id. scripts/console_assign.py now does an existence
-- check on (source_msg_id, ask) before inserting (this same PR); the unique
-- index below is the enforce-in-code backstop for any other writer,
-- per feedback_enforce_process_in_code_not_promises.
--
-- PROBLEM 3 (board scope, cc-fleet-health op#23531, bus #46360, AGREED
-- design): op#23531 makes cc-fleet-health open ONE operator_asks row per
-- CLIENT-channel inbound (oeh, angullia, cosem-tdu, gazzabyte-irsyad, ...) as
-- a no-drop ledger for those channels -- necessary tracking, but NOT an ask
-- of Musa, so it must never show on his board. ask_surface is the agreed
-- scoping column:
--   'operator'       (DEFAULT) -- Musa's own board: traceable to a genuine
--                                 Musa inbound (source_msg_id, set only by
--                                 nervous_system/operator_log.py's
--                                 maybe_track_ask() or a console_assign.py
--                                 link to that same row) or explicitly
--                                 opened via scripts/asks_open.py --ask
--                                 (waiting_on_operator=true). Every
--                                 pre-existing row defaults here -- no row is
--                                 silently reclassified by this migration.
--   'client-channel'  -- cc-fleet-health's op#23531 component-4 writer; a
--                        per-lane/per-channel review view only
--                        (delegated_to=<reviewer lane>), never Musa's board.
--
-- nervous_system/console/db.py's build_asks_query() (this same PR) filters
-- WHERE ask_surface='operator' AND (source_msg_id IS NOT NULL OR
-- waiting_on_operator) -- both the surface AND the traceability condition,
-- so a future writer that forgets to stamp ask_surface correctly still can't
-- leak an untraceable row onto Musa's board.
--
-- Apply via scripts/apply_migration.py 083 --silo tscuymavysscrvoberrr (direct
-- psycopg-apply; decision 962 -- NEVER `supabase db push` against this
-- substrate).

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.operator_asks
  ADD COLUMN IF NOT EXISTS ask_surface text NOT NULL DEFAULT 'operator';

ALTER TABLE public.operator_asks
  ADD CONSTRAINT operator_asks_ask_surface_chk
  CHECK (ask_surface IN ('operator', 'client-channel'));

COMMENT ON COLUMN public.operator_asks.ask_surface IS
  '''operator'' (default) = shows on Musa''s "Your asks" board -- traceable '
  'to a real Musa inbound (source_msg_id) or opened via asks_open.py --ask '
  '(waiting_on_operator). ''client-channel'' = cc-fleet-health''s op#23531 '
  'per-lane review ledger; filtered OFF Musa''s board by '
  'nervous_system/console/db.py build_asks_query(). See migration 083 header.';

-- Dedupe backstop (Musa op#23554 item 2): the SAME (source_msg_id, ask) pair
-- must never be inserted twice, but a single source_msg_id fanning out into
-- several DIFFERENT delegated sub-asks (ids 18/19/20) is legitimate and must
-- keep working -- so the uniqueness is on the PAIR, not on source_msg_id
-- alone. NULLs (untracked/pure delegations) are excluded, matching the
-- PROBLEM 1 fix that stops those from being inserted in the first place.
CREATE UNIQUE INDEX IF NOT EXISTS operator_asks_source_ask_uniq
  ON public.operator_asks (source_msg_id, ask)
  WHERE source_msg_id IS NOT NULL;

COMMIT;
