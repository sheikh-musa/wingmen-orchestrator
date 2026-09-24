-- 066_protected_agents_registry_columns.sql
-- ledger: silo=tscuymavysscrvoberrr
-- (orchestrator substrate)
--
-- P1 of the substrate ihsanification programme (reports/substrate-ihsanification-audit-2026-09-05.md,
-- op#42896/#42909, Nazim rulings bus #42909): ONE REGISTRY. 9 independent hardcoded
-- singleton/protected-agent lists exist across this repo and disagree with each other
-- (see reports/substrate-ihsanification-next-moves-op42896.md for the full inventory) —
-- this migration adds the columns those call sites need in order to read from
-- `protected_agents` instead of defining their own set, per Nazim's ruling:
--   "ONE accessor over protected_agents (+ fleet_lanes for lanes), agent_id-keyed. The 3
--    tmux-session-name sites map through a single agent_id->tmux column/lookup in that
--    accessor, not their own lists."
--
-- Additive only. No existing row is modified in a way that changes current read behaviour
-- (all new columns are nullable / have a safe default) — this migration alone changes
-- nothing about any consumer; the accessor module (nervous_system/protected_agents.py)
-- and each call site's migration are separate, deliberately incremental steps.

ALTER TABLE public.protected_agents
    ADD COLUMN IF NOT EXISTS kind text,
    ADD COLUMN IF NOT EXISTS tmux_session text,
    ADD COLUMN IF NOT EXISTS boots_from_env_only boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.protected_agents.kind IS
    'Free-text classification (e.g. always-on-singleton, auditor-singleton) — informational, not read for any current gate logic.';
COMMENT ON COLUMN public.protected_agents.tmux_session IS
    'The tmux session name this agent_id boots under, where one exists and is confirmed (sourced from scripts/switch_singleton_token.sh''s NODE case statement, not guessed). NULL means unconfirmed/not applicable — callers that need a tmux-session mapping must treat NULL as "unknown", never as "no session".';
COMMENT ON COLUMN public.protected_agents.boots_from_env_only IS
    'True for the specific, narrower set scripts/lib/lane_token_resolver.py calls _NO_POINTER_SINGLETONS ("cai", "fleet-health"): singletons that boot straight off the .env account with NO per-session/group/fleet-default token pointer tier. This is a DIFFERENT semantic dimension than "is a protected singleton" (every row in this table already is one) — it exists so lane_token_resolver''s narrower set can eventually be expressed as a FILTER over this table (per Nazim''s ruling: "never a separate list") instead of the migration happening blind. NOT YET consumed by lane_token_resolver.py in this pass — that file is correctness-critical (its own docstring: "expected==boot BY CONSTRUCTION") and was deliberately left unmigrated pending a dedicated, tested pass rather than folded into this batch.';

-- Backfill: agents present in the most-complete existing hardcoded set
-- (scripts/lib/fleet_health_boundaries.py SINGLETON_BODIES, CAI-RESP-1392) but
-- missing from protected_agents today. Sourced from that file''s own comments,
-- not guessed.
INSERT INTO public.protected_agents (agent_id, reason, added_by)
VALUES
    ('cc-quality', 'auditor/brain singleton (CAI-500) — recycles via its own reset_cc-quality.sh, never the worker-lane path (CAI-RESP-1392)', 'cc-substrate'),
    ('cc-storefront', 'auditor/brain singleton (CAI-500) — recycles via its own reset_cc-storefront.sh, never the worker-lane path (CAI-RESP-1392)', 'cc-substrate'),
    ('cc-finance', 'auditor/brain singleton (CAI-500) — recycles via its own reset_cc-finance.sh, never the worker-lane path (CAI-RESP-1392)', 'cc-substrate')
ON CONFLICT (agent_id) DO NOTHING;

-- tmux_session backfill for the 4 identities switch_singleton_token.sh documents
-- explicitly in its NODE case statement (sourced, not guessed):
UPDATE public.protected_agents SET kind = 'always-on-singleton', tmux_session = 'fleet-health' WHERE agent_id = 'cc-fleet-health';
UPDATE public.protected_agents SET kind = 'always-on-singleton', tmux_session = 'cai', boots_from_env_only = true WHERE agent_id = 'cai';
UPDATE public.protected_agents SET kind = 'always-on-singleton', tmux_session = 'nazim' WHERE agent_id = 'orch-console';
UPDATE public.protected_agents SET kind = 'auditor-singleton', tmux_session = 'quality' WHERE agent_id = 'cc-quality';
UPDATE public.protected_agents SET kind = 'auditor-singleton' WHERE agent_id IN ('cc-storefront', 'cc-finance');
UPDATE public.protected_agents SET kind = 'always-on-singleton' WHERE agent_id = 'cc-orchestrator';
-- 'fleet-health' as a _NO_POINTER_SINGLETONS entry (lane_token_resolver.py) refers to
-- cc-fleet-health's tmux session, not a second agent_id -- no separate row needed; the
-- boots_from_env_only semantics for cc-fleet-health are deliberately NOT set here (see
-- column comment: lane_token_resolver.py itself is not migrated in this pass, so setting
-- this flag now would be an unverified guess about a correctness-critical file's future
-- read path -- left false/default until that file's own migration confirms it).
