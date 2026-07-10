-- shared_feed_enable.cutover.sql — DATA cutover, NOT a schema migration.
-- WAR-ROOM-FEED-001 (+ Hafiz partner feed). Enables the shared-awareness feeds by
-- registering their group chat_ids in group_routing on the channels whose bots
-- poll them. Apply ONLY during the coordinated both-machine restart, AFTER
-- ingest.py (per-chat routing + cross-bot dedup, branch feat/war-room-live-feed)
-- is deployed on BOTH the hub (Studio) and the Mini.
--
-- Two feeds, IDENTICAL mechanism (no per-feed code — SHARED_FEED_TAGS is generic):
--   war-room       chat -5383530504  tag 'war-room'
--   hafiz-partner  chat -5557014342  tag 'hafiz-partner'  (default responder = hub, for now)
-- Both log ONCE (cross-bot dedup), tagged as the feed, and are already carved out
-- of every body's PERSONAL DM reconciliation (operator_log._SHARED_FEED_TAGS).
--
-- Sequencing is LOAD-BEARING — do NOT apply against the old ingest code:
--   * old code ignores group_routing for TAGGING (would keep DM-tagging the posts)
--   * but old code DOES honour allowed_chat_ids, so adding the group ids to the
--     allowlists makes them gate-ALLOWED on every listed bot -> each nudges/routes
--     them 3× with DM tags: exactly the bug this feature removes, amplified.
-- Apply the code first (both machines), then this, then restart. Apply via
-- direct-psycopg (NEVER db push — CLAUDE.md / decision 962).
--
-- ENABLE QUESTION (answered): the war-room bot_channels row STAYS DISABLED — its
-- token is WINGMEN_BOT_TOKEN (SAME as operator-orch), so enabling it = a 2nd poll
-- loop on @wingmennorchbot (dual-poller 409 / offset war). Both feeds are served
-- PURELY by group_routing on the channels that already poll them; no separate feed
-- poll loop. The disabled war-room row remains as an anchor for its chat_id + tag.
--
-- CHANNEL SET — registered on all 3 hub/Mini agent-session channels (operator-orch,
-- cai-channel, nazim-console) as the SAFE SUPERSET: an entry is inert for a bot
-- that isn't a member of the group (it never receives those updates), and correct
-- for one that is. war-room = confirmed all 3 members. hafiz-partner = registered
-- on all 3 pending confirmation of exact bot membership; trim to the member bots
-- if known (harmless either way — flagged in the cc-infra report).

-- ── 1. group_routing: chat_id -> feed tag. '||' MERGES the JSONB, preserving
--       existing keys (nazim-console's nudge_when_busy stays intact). Idempotent.
UPDATE bot_channels
   SET group_routing = group_routing
                       || '{"-5383530504": "war-room"}'::jsonb
                       || '{"-5557014342": "hafiz-partner"}'::jsonb,
       updated_at = now()
 WHERE channel_key IN ('operator-orch', 'cai-channel', 'nazim-console');

-- ── 2. Gate-allow both group chats (routed, not just logged). Guarded so a
--       re-run never duplicates an id in the array.
UPDATE bot_channels
   SET allowed_chat_ids = allowed_chat_ids || ARRAY[-5383530504::bigint],
       updated_at = now()
 WHERE channel_key IN ('operator-orch', 'cai-channel', 'nazim-console')
   AND NOT (-5383530504 = ANY(allowed_chat_ids));

UPDATE bot_channels
   SET allowed_chat_ids = allowed_chat_ids || ARRAY[-5557014342::bigint],
       updated_at = now()
 WHERE channel_key IN ('operator-orch', 'cai-channel', 'nazim-console')
   AND NOT (-5557014342 = ANY(allowed_chat_ids));

-- Verify (read-back) after applying:
--   SELECT channel_key, allowed_chat_ids, group_routing FROM bot_channels
--    WHERE channel_key IN ('operator-orch','cai-channel','nazim-console','war-room')
--    ORDER BY channel_key;
--   Expect: the 3 channels carry "-5383530504":"war-room" AND "-5557014342":
--   "hafiz-partner" in group_routing, and both ids in allowed_chat_ids; war-room
--   row unchanged (enabled=false).
