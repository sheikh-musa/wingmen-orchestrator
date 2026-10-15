-- 106_fleet_lanes_glm_peak_urgent.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- fleet_lanes.glm_peak_urgent — orch-console #67504/#67507 (Musa op#30563): at GLM peak
-- (06:00-10:00Z), a lane doing URGENT work should flip to the Musa/Anthropic pool and keep
-- working; a lane doing non-urgent/backlog work should pause instead (0 pool burn). This
-- column is the per-lane switch the glm_peak_pause_resume.py daemon reads to decide which
-- branch applies to which lane, each time it fires.
--
-- Default FALSE deliberately: an unflagged lane is the SAFE/CHEAP branch (pause, burn
-- nothing) rather than the pool-burning branch (flip to Musa) — a newly-added lane or one
-- the console hasn't gotten around to classifying yet should never silently start consuming
-- the Musa pool at peak just because nobody set a flag.
--
-- Maintained BY THE CONSOLE (orch-console), not Musa and not set-once-forever: the console
-- already tracks which cosem lanes are doing today's hot work, so it updates this flag as
-- priorities shift. The daemon itself needs no human in the loop at fire time — that's what
-- actually satisfies Musa's "shouldn't have to remind every day" ask.

ALTER TABLE fleet_lanes ADD COLUMN IF NOT EXISTS glm_peak_urgent boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN fleet_lanes.glm_peak_urgent IS
  'At GLM peak (06:00-10:00Z): true = flip this lane to the Musa/Anthropic pool and keep it working; false (default) = pause it (0 pool burn) until off-peak. Maintained by orch-console as the day''s hot work shifts. Read by scripts/glm_peak_pause_resume.py.';
