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
-- REVISED per orch-console #52583 (first draft was reviewed and rejected
-- before being applied): the original DEFAULT false + seed-true-for-two
-- polarity would have OPENED UP every other channel -- including
-- gazzabyte-irsyad (minors) and cosem-tdu (NRIC fragments) -- the moment
-- the column existed. It also seeded a channel_key ('cosem-adcda') that
-- does not exist -- the real ADCDA group is cosem-caai -- so that seed
-- would have silently matched nothing.
--
-- New polarity: sensitive_data DEFAULT true (new/unlisted channels are
-- sensitive unless explicitly cleared). FALSE is seeded only for an
-- explicit internal allowlist. First draft (orch-console #52583) seeded
-- only the 3 operator/agent console channels and deliberately left
-- finance-console/war-room out pending an explicit ruling rather than
-- assuming; orch-console confirmed both (#52606) -- bot_channels marks
-- both audience='internal', same tier as the other 3 -- so they're in the
-- allowlist too. Every other existing channel -- including cosem-exams,
-- gazzabyte-irsyad, cosem-tdu, nutri-study, and all client groups -- stays
-- sensitive_data=true.
--
-- Each allowlist key is asserted to exist (RAISE on a missing/typo'd key --
-- a typo must fail loudly, not silently no-op), and the final state is
-- asserted post-apply: count(sensitive_data=false) must equal the
-- allowlist size exactly.

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.bot_channels
  ADD COLUMN IF NOT EXISTS sensitive_data BOOLEAN NOT NULL DEFAULT true;

DO $$
DECLARE
  allowlist text[] := ARRAY['nazim-console', 'operator-orch', 'cai-channel', 'finance-console', 'war-room'];
  k text;
  n_matched int;
  n_cleared int;
BEGIN
  FOREACH k IN ARRAY allowlist LOOP
    IF NOT EXISTS (SELECT 1 FROM public.bot_channels WHERE channel_key = k) THEN
      RAISE EXCEPTION 'migration 091: allowlist channel_key % does not exist in bot_channels -- fix the allowlist, do not let this no-op', k;
    END IF;
  END LOOP;

  UPDATE public.bot_channels SET sensitive_data = false WHERE channel_key = ANY(allowlist);

  SELECT count(*) INTO n_cleared FROM public.bot_channels WHERE sensitive_data = false;
  IF n_cleared != array_length(allowlist, 1) THEN
    RAISE EXCEPTION 'migration 091: post-apply count(sensitive_data=false)=% does not equal allowlist size=% -- aborting', n_cleared, array_length(allowlist, 1);
  END IF;
END $$;

COMMIT;
