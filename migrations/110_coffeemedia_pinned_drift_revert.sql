-- 110_coffeemedia_pinned_drift_revert.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- EMERGENCY REVERT of migration 109 (coffeemedia testbed activation,
-- op#31405, bus #70394/#70398). 'coffeemedia' is ALSO pinned via
-- INGEST_CHANNELS on Sheikhs-Mini (scripts/boot_nazim_ingest.sh), so flipping
-- it to enabled=true made BOTH the Mini's pinned poller AND the hub's
-- enabled-driven WINGMENDEVBOT_TOKEN poller hit Telegram's getUpdates for
-- the SAME token concurrently -- the exact dual-poller 409 class already
-- seen once on 'oeh' (bus #43775/#43833, fixed in PR#178).
--
-- Confirmed LIVE, not theoretical: nervous_system/ingest.py's own runtime
-- watchdog (check_pinned_channels_not_enabled) auto-paged orch-console
-- (bus #70399, P1) within the same CONFIG_REFRESH cycle, and
-- logs/nazim-ingest.log shows real recurring
-- "WINGMENDEVBOT_TOKEN: loop error HTTPError: HTTP Error 409: Conflict"
-- starting 03:06:17Z, ~20s after the restart that picked up migration 109.
--
-- This restores the pre-109 state so the Mini's pinned poller is once again
-- the ONLY consumer of WINGMENDEVBOT_TOKEN, matching the documented
-- invariant ("a channel pinned via INGEST_CHANNELS ships enabled=false").
-- It does NOT decide how the coffeemedia testbed should proceed -- that
-- needs an orch-console call on whether to pull coffeemedia OUT of the
-- Mini's INGEST_CHANNELS pin list first (so the hub can own it per the
-- testbed's actual intent) before re-enabling.

UPDATE bot_channels
SET enabled = false, updated_at = now()
WHERE channel_key = 'coffeemedia' AND token_env_key = 'WINGMENDEVBOT_TOKEN';
