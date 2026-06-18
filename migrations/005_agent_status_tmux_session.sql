-- 005_agent_status_tmux_session.sql — #111 wake-delivery robustness.
-- The orchestrator runs under launchd, where cross-process introspection
-- (mapping a lane's claude pid to its tmux session) is sandbox-restricted and
-- returns nothing — so the auto-wake could never resolve a live lane. Fix: each
-- lane SELF-REGISTERS its tmux session name (which it knows trivially from inside
-- its own pane) into agent_status at boot; the wake then resolves via a pure DB
-- read, zero process introspection. Additive, nullable — no backfill required by
-- the schema (done separately for currently-running lanes).
ALTER TABLE agent_status ADD COLUMN IF NOT EXISTS tmux_session text;
