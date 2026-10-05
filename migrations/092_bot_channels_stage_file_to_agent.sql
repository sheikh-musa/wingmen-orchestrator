-- 092_bot_channels_stage_file_to_agent.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- bus #53147: the ingest watchdog's STAGE-FILE auto-route
-- (nervous_system/ingest.py _page_stage_file_once) hardcodes every client
-- channel's page to orch-console (PAGE_TO_AGENT), even for a channel whose
-- file-handling is already owned end-to-end by a specific lane (cosem-exams
-- -> cc-cosem-exams owns staging+handling Hariz's template files, standing)
-- -- the console was a redundant relay hop for that stream.
--
-- Deliberately NOT derived from the existing bot_channels.owner_lane column:
-- owner_lane is a human/project LABEL (values seen live: 'nazim', 'angullia',
-- 'cosem-port', 'shipforge', 'oeh', 'cosem-tdu-coord') and several of those do
-- NOT map 1:1 onto a real bus agent_id by any simple prefix rule (e.g.
-- owner_lane='cosem-port' on channel cosem-caai, but the real registered
-- agent is cc-cosem-platform, not cc-cosem-port) -- auto-deriving a `to_agent`
-- from it would silently misroute other channels this change was never asked
-- to touch. This column is instead an explicit, per-channel, opt-in override:
-- NULL (every existing row) changes nothing -- _page_stage_file_once keeps
-- falling back to orch-console exactly as today. Only cosem-exams is seeded.
--
-- Scope stays narrow on purpose (orch-console #53147): this only moves the
-- PRE-READ STAGING handoff for that one channel. orch-console remains the
-- reviewer for supervised client REPLIES on every channel, unconditionally
-- -- that path is untouched by this migration and by the ingest.py change
-- that reads this column.

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.bot_channels
  ADD COLUMN IF NOT EXISTS stage_file_to_agent text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.bot_channels WHERE channel_key = 'cosem-exams') THEN
    RAISE EXCEPTION 'migration 092: channel_key ''cosem-exams'' does not exist in bot_channels -- fix the seed, do not let this no-op';
  END IF;
END $$;

UPDATE public.bot_channels SET stage_file_to_agent = 'cc-cosem-exams' WHERE channel_key = 'cosem-exams';

DO $$
DECLARE
  n_set int;
BEGIN
  SELECT count(*) INTO n_set FROM public.bot_channels WHERE stage_file_to_agent IS NOT NULL;
  IF n_set != 1 THEN
    RAISE EXCEPTION 'migration 092: post-apply count(stage_file_to_agent IS NOT NULL)=% does not equal expected 1 -- aborting', n_set;
  END IF;
END $$;

COMMIT;
