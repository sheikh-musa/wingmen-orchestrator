-- 115_operator_messages_media_status.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Musa directive via orch-console (bus #74111): guarantee Telegram media
-- RECEIPT, not just message delivery. Message delivery was already
-- guaranteed at-least-once (poll offset only advances after the durable
-- operator_messages commit; Telegram redelivers on its ~24h buffer;
-- ingest_dedup absorbs the replay). The gap is the media BINARY: in
-- _media_content (nervous_system/ingest.py), a download failure after all
-- 4 retries is caught, the row gets a text marker ("sent a photo, download
-- failed: ..."), the offset still advances (no redelivery), and the
-- Telegram file_id is never persisted anywhere re-fetchable --
-- message_queue.file_id is dead legacy (last row 2026-04-16, unused by
-- current ingest; confirmed via information_schema before writing this).
--
-- These 4 columns let the log write (step 2 of process_update) persist the
-- file_id in THE SAME insert the row always got before, regardless of
-- whether the download attempt inside that same step succeeds -- so a
-- download that fails after all retries leaves a re-fetchable pointer
-- instead of a dead-end text marker. A MEDIA-REAPER daemon (same shape as
-- nervous_system/commitment_sweeper.py) then retries getFile+download for
-- every media_status IN ('pending','failed') row until bytes land and
-- media_status flips to 'ok'.
--
-- media_status is NULL for a text-only row (the CHECK only constrains a
-- non-null value) -- every text message stays exactly as it is today.
-- 'pending' is accepted by the CHECK but has no current producer: a true
-- decouple of the log write from the download attempt (committing the row
-- BEFORE any network call) would need a larger restructure of
-- process_update's commit boundaries and was deliberately left out of this
-- pass -- see nervous_system/media_reaper.py's module docstring.
--
-- media-reaper (new agent identity, registered below) is the writer that
-- flips media_status to 'ok' after ingest.py's own insert; it needs its own
-- row in `agents` before its first agent_messages escalation, same FK
-- precondition as commitment-sweeper (migration 076) / client-nodrop-watch
-- (migration 113).
--
-- All additive (new columns + one partial index + one agent identity).
-- REVERT:
--   DROP INDEX IF EXISTS operator_messages_media_pending_idx;
--   ALTER TABLE public.operator_messages
--     DROP COLUMN file_id, DROP COLUMN file_unique_id,
--     DROP COLUMN media_kind, DROP COLUMN media_status;
--   DELETE FROM agents WHERE id = 'media-reaper';

BEGIN;

ALTER TABLE public.operator_messages
  ADD COLUMN IF NOT EXISTS file_id        text,
  ADD COLUMN IF NOT EXISTS file_unique_id text,
  ADD COLUMN IF NOT EXISTS media_kind     text,
  ADD COLUMN IF NOT EXISTS media_status   text;

ALTER TABLE public.operator_messages
  DROP CONSTRAINT IF EXISTS operator_messages_media_status_check;
ALTER TABLE public.operator_messages
  ADD CONSTRAINT operator_messages_media_status_check
  CHECK (media_status IS NULL OR media_status IN ('pending', 'ok', 'failed'));

-- Reaper's own poll query: media_status IN ('pending','failed'). A partial
-- index keeps that scan cheap as operator_messages grows -- the vast
-- majority of rows (text, or media already 'ok') never match it.
CREATE INDEX IF NOT EXISTS operator_messages_media_pending_idx
  ON public.operator_messages (id)
  WHERE media_status IN ('pending', 'failed');

INSERT INTO agents (id, display_name, status)
VALUES (
    'media-reaper',
    'Telegram media-download reaper (nervous_system/media_reaper.py, bus #74111)',
    'active'
)
ON CONFLICT (id) DO NOTHING;

COMMIT;
