-- 141_operator_messages_media_status_unrecoverable.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Terminal media_status for operator attachments that can NEVER download.
-- held_commitment #964 (source bus #85768, orch-console).
--
-- WHY: migration 115 constrained media_status to (pending, ok, failed) and the
-- media_reaper retries every 'pending'/'failed' row forever. But two Telegram
-- getFile failures are NOT transient and no retry can ever fix them:
--   * 400 "file is too big"   — the Bot API cannot download anything >20 MB
--   * 400 "invalid file_id" / "wrong file identifier" / expired file reference
--   * 404 Not Found           — the file is gone from Telegram's CDN
-- Today those land as 'failed', so the reaper re-pages the console every two
-- hours (ESCALATE_AFTER) about an item that will never recover. The stopgap
-- console used on op#36345 was to set media_status back to NULL, which fakes
-- "this was never media" — a silent_DEAD_END, exactly the class of loss the
-- reaper exists to prevent.
--
-- 'unrecoverable' is a TERMINAL state, distinct from 'failed':
--   failed        = something went wrong, a retry may still work
--   unrecoverable = the operator must resend it (smaller) or we must confirm
--                   it was redundant; retrying is wasted work and the paging
--                   is noise that trains people to ignore the pager
-- The reaper's own poll predicate is UNCHANGED (media_status IN
-- ('pending','failed')), so a row that reaches 'unrecoverable' simply stops
-- being selected. That is the whole point: no reaper code change is needed to
-- stop the retry, only to stop the SILENCE — ingest and the reaper both page
-- ONCE at the moment they classify a row this way.
--
-- Idempotent and reversible, same shape as 115:
--   DROP CONSTRAINT IF EXISTS ... ; ADD CONSTRAINT ... CHECK (...)
--   revert = restore the 115 predicate, with any 'unrecoverable' rows first
--            mapped back to 'failed' (see the note at the bottom).

BEGIN;

ALTER TABLE public.operator_messages
  DROP CONSTRAINT IF EXISTS operator_messages_media_status_check;

ALTER TABLE public.operator_messages
  ADD CONSTRAINT operator_messages_media_status_check
  CHECK (media_status IS NULL
         OR media_status IN ('pending', 'ok', 'failed', 'unrecoverable'));

-- The reaper keeps polling only the retryable pair. Declared explicitly here so
-- a future reader does not assume 'unrecoverable' was forgotten: 115's partial
-- index predicate is intentionally unchanged, which is what makes the terminal
-- state actually terminate the retry loop.
COMMENT ON CONSTRAINT operator_messages_media_status_check ON public.operator_messages IS
  'pending/ok/failed/unrecoverable; unrecoverable is TERMINAL (held_commitment '
  '#964) — the media_reaper never retries it, ingest and the reaper each page '
  'the operator once at classification time';

COMMIT;

-- REVERT (manual, not automated):
--   UPDATE operator_messages SET media_status = 'failed'
--    WHERE media_status = 'unrecoverable';
--   ALTER TABLE operator_messages
--     DROP CONSTRAINT operator_messages_media_status_check;
--   ALTER TABLE operator_messages
--     ADD CONSTRAINT operator_messages_media_status_check
--     CHECK (media_status IS NULL OR media_status IN ('pending','ok','failed'));
-- Reverting restores the re-page-forever behaviour for those rows; do it only
-- if the classification proves wrong, and re-page the operator explicitly
-- rather than letting the reaper resume silently.
