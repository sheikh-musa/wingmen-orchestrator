-- 108_wingmendevbot_testbed_repoint.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Musa's testbed for "one-bot-many-clients" (op#31298/31307/31313, bus #69975,
-- design-GO bus #69978 PR2): repoints the 'oeh' and 'angullia' bot_channels rows
-- from their own dedicated tokens onto the shared WINGMENDEVBOT_TOKEN, alongside
-- the existing 'coffeemedia' row which already uses it. This exercises the new
-- token-group routing (PR#424, merged) with THREE rows sharing one token in the
-- actual production pinned-channel list (boot_nazim_ingest.sh's INGEST_CHANNELS
-- override already polls all three regardless of `enabled`), proving the
-- multi-row-same-token path for real rather than only in a synthetic test.
--
-- Both rows' allowed_chat_ids are left exactly as they are today (disjoint from
-- each other and from coffeemedia's [286619815] -- verified before writing this
-- migration, no overlap), and `enabled` stays FALSE on both per design-GO
-- condition 5: this does NOT make either channel live or send to a real client
-- chat. Musa drives the testbed activation separately, after this lands.
--
-- Old tokens (OEH_BOT_TOKEN / ANGULLIA_BOT_TOKEN) are left untouched in .env --
-- this migration only repoints which env var name the row's token_env_key
-- column references; it does not revoke or touch either bot's token.

UPDATE bot_channels
SET token_env_key = 'WINGMENDEVBOT_TOKEN', updated_at = now()
WHERE channel_key = 'oeh' AND token_env_key = 'OEH_BOT_TOKEN';

UPDATE bot_channels
SET token_env_key = 'WINGMENDEVBOT_TOKEN', updated_at = now()
WHERE channel_key = 'angullia' AND token_env_key = 'ANGULLIA_BOT_TOKEN';
