"""Singleton base ids: bodies that run as exactly ONE identity and are never fanned out into
-N instances (orch-console ruling #77922, 2026-10-27).

A manual launch_dangerous_cc.sh in an orchestrator worktree (orchestrator.wt-cleanup) derived
base 'cc-orchestrator' from pwd and registered itself as 'cc-orchestrator-1' on the Mini. From
then on bus_send refused sends to the hub ("did you mean --to cc-orchestrator-1?") and the
Mini's resolve_tmux_session('cc-orchestrator') returned that worker's pane. For these ids the
base address IS the body: never redirect mail to an instance, never resolve a wake onto one.
"""
SINGLETON_BASES = frozenset({"cc-orchestrator", "orch-console", "cai", "cc-fleet-health", "cc-quality"})


def is_singleton_base(agent_id) -> bool:
    return agent_id in SINGLETON_BASES
