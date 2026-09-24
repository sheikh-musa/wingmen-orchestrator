-- 067_protected_agents_tmux_aliases.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- P1 of the substrate ihsanification programme, continued (op#42896/#42909).
-- Migration 066 gave protected_agents ONE tmux_session per agent_id. That's not
-- enough for cc-orchestrator, which legitimately runs under TWO names depending
-- on state (ORCH-TOPOLOGY-001: "orch" is the live hub session the operator's
-- bridge exact-matches; "orchestrator" is the idle/pre-boot name) -- today both
-- are NULL, which is exactly the hole that blocked migrating
-- scripts/lib/lane_winddown.py and scripts/fleet_model.sh onto the registry
-- (their hardcoded SINGLETONS/CORE_LANES sets are the only place either name is
-- currently protected). Orch-console ruling (bus #43044, 2026-09-24): additive
-- array column + accessor union, singular column's semantics untouched.
--
-- Additive: the new column defaults to an empty array, and only
-- cc-orchestrator's row is backfilled. protected_agent_ids() / protected_agents()
-- (agent_id-keyed) are unaffected -- this column is consumed by a NEW accessor
-- (protected_tmux_sessions(), a separate deliberately-incremental step per
-- migration 066's own precedent), not by any existing call site.
--
-- NOT a zero-behaviour-change migration, named exactly (orch-console gate
-- review, bus #43047): this changes cc-orchestrator.tmux_session NULL -> 'orch',
-- read by nervous_system.protected_agents.tmux_session_for() (protected_agents.py:106).
-- Verified as of commit 2cafc59: tmux_session_for() has NO non-test callers
-- anywhere in this repo today, so nothing breaks -- but a future caller reading
-- that function WILL see 'orch' instead of None after this migration applies.
--
-- REVERT: the backfill changes an existing column value, not just adds one --
-- dropping the new column alone would leave cc-orchestrator.tmux_session='orch'
-- behind, which is NOT the pre-migration state. Full revert:
--   UPDATE public.protected_agents SET tmux_session = NULL
--       WHERE agent_id='cc-orchestrator' AND tmux_session='orch';
--   ALTER TABLE public.protected_agents DROP COLUMN IF EXISTS tmux_session_aliases;

ALTER TABLE public.protected_agents
    ADD COLUMN IF NOT EXISTS tmux_session_aliases text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN public.protected_agents.tmux_session_aliases IS
    'Additional tmux session name(s) this agent_id also boots under, beyond the single tmux_session column (migration 066). Exists because cc-orchestrator legitimately runs under two names ("orch" live, "orchestrator" idle/pre-boot, per ORCH-TOPOLOGY-001) and one TEXT column cannot hold both. Consumed by nervous_system.protected_agents.protected_tmux_sessions() as tmux_session UNION tmux_session_aliases (never a replacement for tmux_session -- that column stays the single confirmed primary session where one exists). Empty array (the default) means "no known aliases", same NULL-is-unknown caution as tmux_session itself: absence here is not a claim that no alias exists, only that none is confirmed yet.';

-- Backfill: cc-orchestrator's two known session names, sourced from
-- ORCH-TOPOLOGY-001 (this repo's CLAUDE.md) and the live-you doctrine ("Never
-- name a non-hub session orch"), not guessed. Both tmux_session and
-- tmux_session_aliases were NULL/empty for this row before this migration --
-- the real hole that blocked lane_winddown.py/fleet_model.sh.
UPDATE public.protected_agents
    SET tmux_session = 'orch', tmux_session_aliases = ARRAY['orchestrator']
    WHERE agent_id = 'cc-orchestrator';
