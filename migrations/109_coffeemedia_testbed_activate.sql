-- 109_coffeemedia_testbed_activate.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Musa (op#31405, nazim-console, LIVE tonight, bus #70394): activate the
-- "one-bot-many-clients" testbed on 'coffeemedia' FIRST, before either client
-- testbed (oeh/angullia, migration 108) goes live. coffeemedia is an
-- INTERNAL channel -- allowed_chat_ids=[286619815] is Musa's own chat, zero
-- client exposure.
--
-- coffeemedia is already on WINGMENDEVBOT_TOKEN (pre-existing, unrelated to
-- migration 108's repoint) but was never enabled. Flipping it to enabled=true
-- makes it the FIRST enabled channel on that shared token, which is the
-- documented restart-to-join case for the token-group ingest (PR#424): the
-- WINGMENDEVBOT_TOKEN poll task only spins up once at least one of its rows
-- is enabled.
--
-- oeh and angullia are deliberately left untouched here -- still enabled=
-- false per migration 108's design-GO condition 5. Musa drives those
-- separately; this migration activates coffeemedia ONLY.

UPDATE bot_channels
SET enabled = true, updated_at = now()
WHERE channel_key = 'coffeemedia' AND token_env_key = 'WINGMENDEVBOT_TOKEN';
