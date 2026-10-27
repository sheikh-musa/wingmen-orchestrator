"""Singleton base ids: bodies that run as exactly ONE identity and are never fanned out into
-N instances (orch-console ruling #77922, 2026-10-27).

A manual launch_dangerous_cc.sh in an orchestrator worktree (orchestrator.wt-cleanup) derived
base 'cc-orchestrator' from pwd and registered itself as 'cc-orchestrator-1' on the Mini. From
then on bus_send refused sends to the hub ("did you mean --to cc-orchestrator-1?") and the
Mini's resolve_tmux_session('cc-orchestrator') returned that worker's pane. For these ids the
base address IS the body: never redirect mail to an instance, never resolve a wake onto one.

Sourced from the protected_agents registry (kind '*-singleton'), never a hardcoded list
(bus #42909). On an untrusted read it falls back to the registry's own static floor, which is
a superset: over-treating an id as a singleton only means "never redirect it".
"""
import time

_TTL_S = 60
_cache = {"at": 0.0, "ids": None}


def singleton_base_ids(dsn=None, now=None) -> frozenset:
    now = time.time() if now is None else now
    if _cache["ids"] is not None and now - _cache["at"] < _TTL_S:
        return _cache["ids"]
    from nervous_system import protected_agents as pa
    rows = pa._safe_registry_rows(dsn)
    if rows is None:
        ids = pa.protected_agent_ids(dsn)
    else:
        ids = frozenset(a.agent_id for a in rows if (a.kind or "").endswith("-singleton"))
    _cache.update(at=now, ids=ids)
    return ids


def is_singleton_base(agent_id) -> bool:
    return bool(agent_id) and agent_id in singleton_base_ids()
