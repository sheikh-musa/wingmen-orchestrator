-- 107_tg_out_dead_replay_identity.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- Registers the 'tg-out-dead-replay' agent identity (orch-console PR#423 review,
-- bus #69877): nervous_system/tg_out.py's replay_once_then_purge() (held_commitment
-- #314) posts an attributable bus note via scripts/bus_send.py's send() on every
-- dead-row purge. It runs as a bare launchd daemon on the Mac Mini with no
-- AGENT_ID/CC_BASE_AGENT_ID in its environment, so it cannot resolve a live
-- Claude-Code-session identity — and the hub's own 'cc-orchestrator' would be a
-- misattribution (ORCH-TOPOLOGY-001: that id names the VPS hub process, not this
-- Mac Mini daemon). Mirrors the existing 'sla-watchdog' / 'ddl-coverage-watchdog' /
-- 'ingest-watchdog' precedent: a dedicated, honestly-named identity, pre-registered
-- here (not self-registered in the daemon) so send()'s INSERT ... agent_messages
-- FK (from_agent -> agents.id) is satisfiable.

INSERT INTO agents (id, display_name, status)
VALUES (
    'tg-out-dead-replay',
    'tg_out dead-row replay-once-then-purge daemon (nervous_system/tg_out.py, held_commitment #314)',
    'active'
)
ON CONFLICT (id) DO NOTHING;
