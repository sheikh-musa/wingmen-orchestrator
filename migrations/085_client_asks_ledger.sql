-- 085_client_asks_ledger.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Client-ask ledger (Musa op#23944, bus #47105 -> #47114 AGREED design).
-- Shuq (irsyad director) asked for 2 features 29 Sep 01:47Z (operator_messages
-- 23194, chat gazzabyte-irsyad) and it sat ~44h with no date and nothing
-- flagged it -- operator_asks had ZERO rows for any client-channel message;
-- the ledger only ever captured operator surfaces (nervous_system/
-- operator_log.py's maybe_track_ask(), migration 044/082).
--
-- SUPERSEDES cc-fleet-health's op#23531 component-4 (the "per-lane review
-- ledger, ask_surface='client-channel', never Musa's board" design already
-- reserved by migration 084's CHECK constraint -- agreed 2026-09-30, never
-- built by anyone; confirmed via a full-repo grep before this PR). orch-
-- console bus #47110 decision 1: ONE mechanism, this one, reusing the
-- EXISTING 'client-channel' value -- no CHECK-constraint churn on ask_surface.
--
-- WHAT THIS ADDS:
--  1. bot_channels.audience  text CHECK (operator|client|internal) -- the
--     "who is this channel for" fact bot_channels never had. Previously only
--     implicit and scattered across nervous_system/operator_log.py's
--     _LANE_OWNED_TAGS / _CONSOLE_POLLED_CLIENT_TAGS, which mix real client
--     channels with internal drill/editor tags (those lists encode "who
--     reconciles this," not "who the audience is"). Backfilled below per the
--     approved list (bus #47112 -> #47114 APPROVED as written), including
--     hk-editor=client -- overriding orch-console's stated
--     internal-unless-shown-otherwise default, on the evidence that
--     scripts/client_channel_nodrop_watch.py's own test already treats
--     hk-editor as a client case (op#18003).
--  2. bot_channels.owner_lane text -- the lane a client ask on this channel
--     delegates/pages to. Copied from each row's EXISTING inject_target
--     where one exists (the same tmux session that already receives the
--     ingest nudge for that channel) -- no new judgment call. NULL for
--     operator/internal rows with no delegated-client-work concept.
--  3. operator_asks.committed_date timestamptz -- a client ask counts as
--     answered with a date ONLY if that date was actually SENT to the
--     client (bus #47114 item 3a): CHECK enforces committed_date requires a
--     matching outbound_msg_id on the SAME row -- a date agreed only on the
--     bus never satisfies this. scripts/asks_triage.py (this same PR) is the
--     only writer that sets both together.
--
-- Capture (code, this same PR): nervous_system/operator_log.py's new
-- maybe_track_client_ask(), called from nervous_system/ingest.py for every
-- inbound message on a ch.audience='client' channel -- ask_surface=
-- 'client-channel', delegated_to=owner_lane, chase_by REQUIRED at open time
-- (default 24h) -- unlike the operator path's optional chase_by (072).
--
-- IDEMPOTENCE: every statement can re-run safely -- ADD COLUMN uses IF NOT
-- EXISTS, the backfill UPDATEs are guarded by "AND audience IS NULL", and
-- the CHECK constraints use the same pg_constraint existence-guard DO-block
-- shape as migration 084.
--
-- Apply via scripts/apply_migration.py 085 --silo tscuymavysscrvoberrr
-- (direct psycopg-apply; decision 962 -- NEVER supabase db push against this
-- substrate).

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. bot_channels.audience + owner_lane ──────────────────────────────────

ALTER TABLE public.bot_channels
  ADD COLUMN IF NOT EXISTS audience   text,
  ADD COLUMN IF NOT EXISTS owner_lane text;

-- Approved list, bus #47112 -> #47114. owner_lane = existing inject_target
-- except where noted otherwise in the bus thread.
UPDATE public.bot_channels SET audience='client',   owner_lane='nazim'           WHERE channel_key='alderei'          AND audience IS NULL;
UPDATE public.bot_channels SET audience='client',   owner_lane='angullia'        WHERE channel_key='angullia'         AND audience IS NULL;
UPDATE public.bot_channels SET audience='client',   owner_lane='cosem-port'      WHERE channel_key='cosem-caai'       AND audience IS NULL;
UPDATE public.bot_channels SET audience='client',   owner_lane='exams'          WHERE channel_key='cosem-exams'      AND audience IS NULL;
UPDATE public.bot_channels SET audience='client',   owner_lane='cosem-tdu-coord' WHERE channel_key='cosem-tdu'        AND audience IS NULL;
UPDATE public.bot_channels SET audience='client',   owner_lane='irsyad-coord'    WHERE channel_key='gazzabyte-irsyad' AND audience IS NULL;
UPDATE public.bot_channels SET audience='client',   owner_lane='oeh'             WHERE channel_key='oeh'              AND audience IS NULL;
UPDATE public.bot_channels SET audience='client',   owner_lane='shipforge'       WHERE channel_key='hk-editor'        AND audience IS NULL;
UPDATE public.bot_channels SET audience='operator'                               WHERE channel_key='operator-orch'    AND audience IS NULL;
UPDATE public.bot_channels SET audience='operator'                               WHERE channel_key='cai-channel'      AND audience IS NULL;
UPDATE public.bot_channels SET audience='operator'                               WHERE channel_key='nazim-console'    AND audience IS NULL;
UPDATE public.bot_channels SET audience='internal', owner_lane='finance'         WHERE channel_key='finance-console'  AND audience IS NULL;
UPDATE public.bot_channels SET audience='internal'                               WHERE channel_key='war-room'         AND audience IS NULL;
UPDATE public.bot_channels SET audience='internal'                               WHERE channel_key='mamadah'          AND audience IS NULL;
UPDATE public.bot_channels SET audience='internal'                               WHERE channel_key='nutri-study'      AND audience IS NULL;

-- A future channel row must classify audience explicitly at insert time --
-- a control that needs remembering is a sentence; this NOT NULL is the
-- code-enforced version. The backfill above covers every row live today
-- (verified: 15/15 before this migration).
ALTER TABLE public.bot_channels ALTER COLUMN audience SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.bot_channels'::regclass
      AND conname  = 'bot_channels_audience_chk'
  ) THEN
    ALTER TABLE public.bot_channels
      ADD CONSTRAINT bot_channels_audience_chk
      CHECK (audience IN ('operator', 'client', 'internal'));
  END IF;
END $$;

COMMENT ON COLUMN public.bot_channels.audience IS
  'Who this channel is FOR: ''operator'' (Musa''s own DM surfaces), ''client'' '
  '(a business relationship -- feeds the client-ask ledger, migration 085), '
  '''internal'' (fleet/consumer-bot surfaces, no ask-ledger tracking). Set '
  'explicitly per channel, never derived from a tag list.';
COMMENT ON COLUMN public.bot_channels.owner_lane IS
  'The lane/tmux session a client ask on this channel delegates/pages to '
  '(operator_asks.delegated_to). NULL for operator/internal channels with no '
  'delegated-client-work concept. See migration 085.';

-- ── 2. operator_asks.committed_date (outbound-confirmed only) ─────────────

ALTER TABLE public.operator_asks
  ADD COLUMN IF NOT EXISTS committed_date timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.operator_asks'::regclass
      AND conname  = 'operator_asks_committed_date_needs_outbound_chk'
  ) THEN
    ALTER TABLE public.operator_asks
      ADD CONSTRAINT operator_asks_committed_date_needs_outbound_chk
      CHECK (committed_date IS NULL OR outbound_msg_id IS NOT NULL);
  END IF;
END $$;

COMMENT ON COLUMN public.operator_asks.committed_date IS
  'A dated commitment for THIS ask, valid only if it was actually SENT '
  '(outbound_msg_id set on the SAME row -- CHECK enforces the pairing). A '
  'date agreed only on the bus never satisfies this. Set only via '
  'scripts/asks_triage.py. See migration 085 / bus #47114 item 3a.';

-- Chase-net lookup: client-channel rows past chase_by, still open.
CREATE INDEX IF NOT EXISTS operator_asks_client_chase_idx
  ON public.operator_asks (chase_by)
  WHERE closed_at IS NULL AND ask_surface = 'client-channel';

COMMIT;
