-- 072_operator_asks_tracking.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- APPLIED LIVE 2026-09-27 09:28:19Z by a fork WITHOUT gate; ratified
-- retroactively by orch-console bus #43869.
--
-- "Operator asks" reliability fix (Musa: "if I ask 1000 things I expect you to
-- track 1001"; orch-console design, bus thread d0533248-2d25-484f-84ad-3cdd08fe1fce,
-- bus #43749/#43765/#43767).
--
-- DESIGN INVARIANT PRESERVED from migration 044: status is NEVER stored on
-- operator_asks — it is still derived live from the linked agent_messages thread
-- (nervous_system/console/db.py build_asks_query()/fetch_asks()). The three new
-- columns below are STRUCTURAL FACTS, exactly like the existing `delegated_to`
-- column, not a status cache:
--   waiting_on_operator — a fact about who the ball is with (set when a lane/body
--                         opens an ask that specifically needs Musa's input, via
--                         the new scripts/asks_open.py), not a computed status.
--   chase_by            — a fact: the timestamp after which this ask should be
--                         re-raised if still open (optional, set at open time).
--   outbound_msg_id      — a fact: which operator_messages row (our OWN outbound)
--                         this ask is linked to, so a genuine Telegram reply to
--                         THAT message can auto-close the ask (reply-linked
--                         auto-close, piece 1). Not a real FK — operator_messages
--                         and operator_asks already have no cross-table FK
--                         (source_msg_id is the precedent; see 044's own comment
--                         on why: no cross-DB constraint is even possible here,
--                         and in-DB this keeps the same house style).
--
-- tg_message_id on operator_messages is the Telegram-native message_id captured
-- from the Bot API sendMessage response (result.message_id) — needed to detect a
-- genuine `reply_to_message` pointing at OUR row, as opposed to a bare inbound
-- message that merely happens to arrive after an open ask.
--
-- Pure additive DDL (new nullable columns + a CASE branch in application SQL,
-- not in this file) — no privilege changes or function removal, so no
-- `-- assert:` lines are needed (house convention per migrations 065/069/070/071
-- headers; apply_migration.py only requires them for a migration that actually
-- revokes a grant or drops a function).
--
-- Apply via scripts/apply_migration.py 072 --silo tscuymavysscrvoberrr (direct
-- psycopg-apply; decision 962 — NEVER `supabase db push` against this substrate).

BEGIN;

ALTER TABLE public.operator_asks
  ADD COLUMN IF NOT EXISTS waiting_on_operator boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS chase_by            timestamptz,
  ADD COLUMN IF NOT EXISTS outbound_msg_id     bigint;

COMMENT ON COLUMN public.operator_asks.waiting_on_operator IS
  'Structural fact: this ask specifically needs Musa''s input (set via scripts/asks_open.py). NOT a derived/cached status — see migration 044''s status-never-stored invariant.';
COMMENT ON COLUMN public.operator_asks.chase_by IS
  'Structural fact: optional re-chase deadline set at open time (scripts/asks_open.py --chase-hours N). Read by the priority_sla_watchdog asks-chase extension.';
COMMENT ON COLUMN public.operator_asks.outbound_msg_id IS
  'operator_messages.id of OUR outbound message this ask is linked to (no FK — matches source_msg_id''s existing no-cross-constraint house style). A genuine Telegram reply to that row''s tg_message_id auto-closes this ask.';

-- The watchdog's chase sweep filters on open + waiting_on_operator asks.
CREATE INDEX IF NOT EXISTS operator_asks_waiting_idx
  ON public.operator_asks (chase_by)
  WHERE closed_at IS NULL AND waiting_on_operator;

ALTER TABLE public.operator_messages
  ADD COLUMN IF NOT EXISTS tg_message_id bigint;

COMMENT ON COLUMN public.operator_messages.tg_message_id IS
  'Telegram Bot API result.message_id for an outbound send (captured in scripts/_tg_chunked_send.py). Lets an inbound reply_to_message be matched back to the exact row it replied to (reply-linked ask auto-close).';

-- Reply-matching does a point lookup by tg_message_id; keep it cheap.
CREATE INDEX IF NOT EXISTS operator_messages_tg_message_id_idx
  ON public.operator_messages (tg_message_id) WHERE tg_message_id IS NOT NULL;

COMMIT;
