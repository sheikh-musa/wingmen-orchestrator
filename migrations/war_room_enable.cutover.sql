-- war_room_enable.cutover.sql — DATA cutover, NOT a schema migration.
-- WAR-ROOM-FEED-001. Apply ONLY during the coordinated both-machine restart,
-- AFTER ingest.py (per-chat routing + cross-bot dedup, branch feat/war-room-live-feed)
-- is deployed on BOTH the hub (Studio) and the Mini.
--
-- Sequencing is LOAD-BEARING — do NOT apply against the old ingest code:
--   * The old code ignores group_routing for TAGGING, so it would keep tagging
--     war-room posts with each bot's DM tag.
--   * But the old code DOES honour allowed_chat_ids, so adding -5383530504 to the
--     3 allowlists makes war-room gate-ALLOWED on all 3 bots -> each nudges/routes
--     it 3× with DM tags: exactly the bug this feature removes, amplified.
-- Apply the code first (both machines), then this, then restart. Apply via
-- direct-psycopg (NEVER db push — CLAUDE.md / decision 962).
--
-- ENABLE QUESTION (answered): the war-room bot_channels row STAYS DISABLED.
-- Its token is WINGMEN_BOT_TOKEN — the SAME token as operator-orch — so enabling
-- it would spawn a 2nd poll loop on @wingmennorchbot (dual-poller 409 / offset
-- war, the Jul-3 class). War-room is served PURELY by group_routing on the 3
-- channels that already poll the group; no separate war-room poll loop. The
-- disabled row remains as the anchor documenting the chat_id + 'war-room' tag.
-- (This file deliberately does NOT flip war-room.enabled.)

-- 1. Route the war-room group_id -> 'war-room' tag on the 3 receiving channels.
--    '||' MERGES the JSONB, preserving existing keys (nazim-console's
--    nudge_when_busy stays intact). Idempotent — re-running just re-sets the key.
UPDATE bot_channels
   SET group_routing = group_routing || '{"-5383530504": "war-room"}'::jsonb,
       updated_at = now()
 WHERE channel_key IN ('operator-orch', 'cai-channel', 'nazim-console');

-- 2. Gate-allow the war-room chat on those channels (routed, not just logged).
--    Guarded so a re-run never duplicates the id in the array.
UPDATE bot_channels
   SET allowed_chat_ids = allowed_chat_ids || ARRAY[-5383530504::bigint],
       updated_at = now()
 WHERE channel_key IN ('operator-orch', 'cai-channel', 'nazim-console')
   AND NOT (-5383530504 = ANY(allowed_chat_ids));

-- Verify (read-back) after applying:
--   SELECT channel_key, allowed_chat_ids, group_routing FROM bot_channels
--    WHERE channel_key IN ('operator-orch','cai-channel','nazim-console','war-room')
--    ORDER BY channel_key;
--   Expect: the 3 channels carry "-5383530504":"war-room" in group_routing and
--   -5383530504 in allowed_chat_ids; war-room row unchanged (enabled=false).
