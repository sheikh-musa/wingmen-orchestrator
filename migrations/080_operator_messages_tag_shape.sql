-- 080_operator_messages_tag_shape.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- CHECK-constrain operator_messages.tag (bus #44966/#44971/#44997/#45012/#45020,
-- orch-console APPROVED after live-data verification). Fixes the mechanism behind
-- op#16353's leaked-draft rows: every fleet send helper is TEXT-FIRST
-- (`<script> "<text>" [tag]`), funneling through nervous_system/operator_log.py's
-- log() with zero shape/length validation on --tag before the INSERT. The existing
-- send_arg_guard.sh only ever checked arg1 (TEXT) against a hardcoded channel-name
-- allowlist -- it never validated arg2 (TAG)'s *shape*, so a caller landing prose
-- (or a raw Telegram chat_id) in the tag slot sailed straight through to the row.
--
-- PATTERN, verified against live data, not just code literals (bus #45019's
-- self-caught near-miss: the FIRST pattern this session proposed --
-- ^[a-z][a-z0-9_-]*$ -- was checked only against tag literals found in code and
-- would have silently moved ~2,000 legitimate @-prefixed routing tags (@ihsanos,
-- @cai, @adcda, ... plus 'fleet/substrate' and one 'catchup+adcda-hariz-syed-intel'
-- tag) into tag_overflow on the very next step -- a vastly larger blast radius than
-- the 3-row incident it was meant to fix. Running the full `SELECT tag, count(*)
-- ... GROUP BY tag` against operator_messages before writing this file is what
-- caught it):
--   CHECK (tag IS NULL OR (char_length(tag) <= 64
--          AND tag ~ '^[a-zA-Z@][a-zA-Z0-9@_/+-]*$'))
--
-- The two failure shapes this isolates, confirmed to be EXACTLY 5 rows fleet-wide
-- (ids 16347/16348/16352/1675/1758) -- the DO block below aborts the whole
-- transaction if that set has drifted since this file was written, per
-- orch-console's explicit "confirm 0 other failing rows first" condition:
--   (a) op#16353 displaced drafts (16347/16348/16352, chat_id=-5330147776 /
--       gazzabyte-irsyad): the real reply text landed in the tag column instead
--       of being sent, because send_arg_guard.sh's arg1-only check didn't catch
--       tag-slot prose.
--   (b) a separate, older 2026-06-30 incident (1675/1758): a raw Telegram chat_id
--       (e.g. '-5383530504') was passed as the tag instead of a channel name.
-- Both are preserved, not discarded: tag_overflow holds the original value,
-- `text` (the actual message content) is untouched either way. Per orch-console's
-- #45020 ruling, the 2 chat_id-shaped rows get tag=NULL (no inferred destination --
-- a NULL tag + the preserved original is honest); the 3 op#16353 rows already have
-- a legible canonical destination (gazzabyte-irsyad) from their own chat_id, so
-- they're canonicalized to it.
--
-- REVERT:
--   ALTER TABLE public.operator_messages DROP CONSTRAINT IF EXISTS operator_messages_tag_shape_chk;
--   -- tag_overflow intentionally NOT dropped on revert -- it's the only remaining
--   -- record of the 5 rows' original (leaked/malformed) tag values.

BEGIN;

-- Abort the whole migration if the failing-row set has drifted since this file
-- was authored (new bad data, or the 5 known rows already cleaned up some other
-- way) -- orch-console's explicit precondition, not a "trust the comment" gate.
DO $$
DECLARE
  bad_ids bigint[];
BEGIN
  SELECT array_agg(id ORDER BY id) INTO bad_ids
  FROM operator_messages
  WHERE tag IS NOT NULL
    AND NOT (char_length(tag) <= 64 AND tag ~ '^[a-zA-Z@][a-zA-Z0-9@_/+-]*$');

  IF bad_ids IS DISTINCT FROM ARRAY[1675, 1758, 16347, 16348, 16352]::bigint[] THEN
    RAISE EXCEPTION
      '080 PRECONDITION FAILED: rows failing the tag-shape check are % (expected exactly [1675,1758,16347,16348,16352]). Aborting -- scope has drifted since this migration was written.',
      bad_ids;
  END IF;
END $$;

ALTER TABLE public.operator_messages
  ADD COLUMN IF NOT EXISTS tag_overflow text;

COMMENT ON COLUMN public.operator_messages.tag_overflow IS
  'Original tag value displaced by the tag-shape guard (migration 080). Two shapes: '
  '(1) op#16353 arg-order bug -- the actual reply text landed here instead of being '
  'sent, tag was canonicalized to the channel the chat_id resolves to; (2) 2026-06-30 '
  'chat_id-in-tag rows -- tag set to NULL (no inferred destination), original chat_id '
  'preserved here. `text` is untouched in both cases -- this column is provenance '
  'only, never re-queried for tag matching.';

-- (1) op#16353 displaced drafts: canonicalize tag to the resolved destination,
-- preserve the leaked draft text in tag_overflow.
UPDATE public.operator_messages
SET tag_overflow = tag,
    tag = 'gazzabyte-irsyad'
WHERE id IN (16347, 16348, 16352);

-- (2) 2026-06-30 chat_id-shaped tags: tag -> NULL (no inferred destination per
-- orch-console #45020), original chat_id-as-tag preserved.
UPDATE public.operator_messages
SET tag_overflow = tag,
    tag = NULL
WHERE id IN (1675, 1758);

-- Re-verify inline, inside the same transaction, that the 5 known rows now pass
-- and nothing else newly fails -- belt-and-braces on top of the precondition DO
-- block above (that checked BEFORE the UPDATEs; this checks AFTER, right before
-- the CHECK would otherwise fail the same thing less legibly).
DO $$
DECLARE
  remaining_bad bigint;
BEGIN
  SELECT count(*) INTO remaining_bad
  FROM operator_messages
  WHERE tag IS NOT NULL
    AND NOT (char_length(tag) <= 64 AND tag ~ '^[a-zA-Z@][a-zA-Z0-9@_/+-]*$');

  IF remaining_bad <> 0 THEN
    RAISE EXCEPTION '080 POST-UPDATE FAILED: % row(s) still fail the tag-shape check after the canonicalization UPDATEs.', remaining_bad;
  END IF;
END $$;

ALTER TABLE public.operator_messages
  ADD CONSTRAINT operator_messages_tag_shape_chk
  CHECK (tag IS NULL OR (char_length(tag) <= 64 AND tag ~ '^[a-zA-Z@][a-zA-Z0-9@_/+-]*$'));

COMMIT;
