-- 113_client_nodrop_watch_identity.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Registers the 'client-nodrop-watch' agent identity. scripts/client_channel_nodrop_watch.py
-- runs as a bare launchd daemon (dev.wingmen.client-nodrop-watch, PR#444, held_commitment
-- #314) with no AGENT_ID/CC_BASE_AGENT_ID in its environment, so its _bus() helper's
-- hardcoded from_agent='client-nodrop-watch' INSERT ... agent_messages was unsatisfiable
-- against the from_agent -> agents.id FK: the first armed run (2026-10-20, immediately
-- after the PR#444 reload) crashed with psycopg2.errors.ForeignKeyViolation on its first
-- real page attempt (hk-editor stale-backlog nudge), before _save_state() could persist
-- that cycle's dedup state -- caught live, not by promise ("a printed safeguard is not a
-- ran safeguard"). Mirrors the existing 'sla-watchdog' / 'ddl-coverage-watchdog' /
-- 'ingest-watchdog' / 'tg-out-dead-replay' precedent (migration 107): a dedicated,
-- honestly-named identity, pre-registered here (not self-registered in the daemon).

INSERT INTO agents (id, display_name, status)
VALUES (
    'client-nodrop-watch',
    'client-channel no-drop watch daemon (scripts/client_channel_nodrop_watch.py, held_commitment #314)',
    'active'
)
ON CONFLICT (id) DO NOTHING;
