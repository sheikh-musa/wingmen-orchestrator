-- 091_bot_channels_sensitive_data.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- bus #52465: stage_client_file.py's per-channel sensitive override needs a
-- config column to read, not a hardcoded list baked into the script --
-- "read from config" was explicit in the ask. A sensitive channel's --export
-- never writes full content, even on a CLEAN verdict (see
-- export_structure_only in stage_client_file.py) -- heuristics alone must
-- never decide what leaves a raw file on a gov/client-data channel.
--
-- Seeded TRUE for cosem-exams and cosem-adcda (the two channels named in the
-- real incident this closes -- a trainee gradebook screenshot and a progress
-- report, both exam/assessment data). Default FALSE for every other
-- existing row -- this is an opt-IN override on top of the stager's own
-- content heuristics, not a replacement for them.

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.bot_channels
  ADD COLUMN IF NOT EXISTS sensitive_data BOOLEAN NOT NULL DEFAULT false;

UPDATE public.bot_channels SET sensitive_data = true WHERE channel_key = 'cosem-exams';
UPDATE public.bot_channels SET sensitive_data = true WHERE channel_key = 'cosem-adcda';

COMMIT;
