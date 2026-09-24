"""protected_agents.py — the ONE registry accessor (op#42896/#42909, P1).

THE PROBLEM THIS FIXES: independent hardcoded singleton/protected-agent lists
existed across this repo and DISAGREED (e.g. app.py's own literal set was
missing 'cai' before a union with a second source papered over it; full
inventory + migration status in reports/substrate-ihsanification-next-moves-
op42896.md, kept current, not this docstring). Every consumer of "which agents
are protected singletons" should import from HERE instead of defining its own
set — that is the entire point: one source, read everywhere. As of 2026-09-24,
migrated: nervous_system/cc_session_costs_auto_writer.py, scripts/flip_fleet.sh,
scripts/verify_fleet_token.py. Remaining: see tests/test_protected_agents_registry.py's
_KNOWN_HARDCODED_LIST_FILES (the live enforcement tracking, not a comment here).

Backed by the `protected_agents` table (migration 066_protected_agents_registry_columns.sql
added `kind`, `tmux_session`, `boots_from_env_only` on top of the pre-existing
agent_id/reason/added_by/added_at columns).

Fail-safe default: on any DB error, `protected_agent_ids()` returns a hardcoded
FALLBACK set (the union of every known hardcoded list as of 2026-09-24) rather
than an empty set — this guards destructive-op protection logic (a query that
returns "nobody is protected" on a transient DB blip would be catastrophic; a
stale-but-safe fallback is the correct fail mode here, mirroring app.py's own
existing `except Exception` fallback pattern this module is replacing).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import psycopg
from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# Fail-safe fallback ONLY — used if the DB is unreachable. Kept in sync manually
# is exactly the anti-pattern this module exists to end; this copy is allowed to
# exist BECAUSE it's a dead-man default, not a live read path, and its staleness
# can only ever make protection MORE conservative (a name present here but later
# removed from the DB still gets treated as protected until this fallback is
# next updated — never the other way around).
_FALLBACK_PROTECTED = frozenset({
    "cc-orchestrator", "cai", "orch-console", "cc-fleet-health",
    "cc-quality", "cc-storefront", "cc-finance", "nazim-console",
})

# Protected tmux SESSION names that do NOT correspond to any claude agent, and
# so deliberately do NOT go into the `protected_agents` table (orch-console
# ruling, bus #43044, op#42896/#42909 P1 continuation) — that registry stays
# agents-only (agent_id vocabulary); nothing here gets a fake agent_id. Today
# this is a single entry: "fleet-console" is the launchd Python server
# (dev.wingmen.fleet-console, scripts/fleet_model.sh:27) that a lane-winddown
# path must never mistake for an idle worker lane, even though it has no
# agent_id at all. See test_protected_agents_registry.py for the enforcement
# test asserting this never leaks into protected_agent_ids().
PROTECTED_NON_AGENT_SESSIONS = ("fleet-console",)

# Fail-safe fallback for protected_tmux_sessions() ONLY -- same dead-man
# rationale as _FALLBACK_PROTECTED above (staleness can only ever make this
# MORE conservative). This is the union of every tmux-session name either
# scripts/lib/lane_winddown.py's SINGLETONS or scripts/fleet_model.sh's
# CORE_LANES protected before their op#42896/#42909 P1 migration.
_FALLBACK_PROTECTED_SESSIONS = frozenset({
    "nazim", "cai", "orch", "orchestrator", "fleet-health",
    "fleet-console", "quality",
})


@dataclass(frozen=True)
class ProtectedAgent:
    agent_id: str
    kind: Optional[str]
    tmux_session: Optional[str]
    boots_from_env_only: bool
    reason: Optional[str]
    tmux_session_aliases: tuple[str, ...] = ()


def _connect(dsn: Optional[str] = None):
    return psycopg.connect(dsn or os.environ["DATABASE_URL"])


def protected_agents(dsn: Optional[str] = None) -> list[ProtectedAgent]:
    """Full rows from the registry. Raises on a genuine DB error — callers that
    need the fail-safe behaviour should use `protected_agent_ids()` instead,
    which catches and falls back; this function is for callers that want to know
    about a real failure rather than silently degrade.

    `dsn`: pass explicitly when the caller already manages its own connection
    string (e.g. a writer that can run against a host-specific DSN, not
    necessarily this process's own DATABASE_URL) — defaults to DATABASE_URL
    only when omitted."""
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT agent_id, kind, tmux_session, boots_from_env_only, reason, "
                "tmux_session_aliases "
                "FROM public.protected_agents"
            )
            rows = cur.fetchall()
        return [
            ProtectedAgent(*row[:5], tmux_session_aliases=tuple(row[5] or ()))
            for row in rows
        ]
    finally:
        conn.close()


def protected_agent_ids(dsn: Optional[str] = None) -> frozenset[str]:
    """The set every consumer that just needs membership-testing should use.
    Fails safe (see module docstring) to the hardcoded union fallback on any
    DB error — never returns an empty set, never raises."""
    try:
        return frozenset(a.agent_id for a in protected_agents(dsn))
    except Exception:  # noqa: BLE001 — dead-man fallback, must never propagate
        return _FALLBACK_PROTECTED


def tmux_session_for(agent_id: str, dsn: Optional[str] = None) -> Optional[str]:
    """The tmux session name this agent_id boots under, or None if unconfirmed.
    None must be treated as "unknown", never as "this agent has no session" —
    several agent_ids have a real session but it isn't sourced/confirmed yet
    (see migration 066's column comment)."""
    try:
        for a in protected_agents(dsn):
            if a.agent_id == agent_id:
                return a.tmux_session
    except Exception:  # noqa: BLE001
        pass
    return None


def protected_tmux_sessions(dsn: Optional[str] = None) -> frozenset[str]:
    """The set of tmux SESSION NAMES a winddown/model-flip/etc. path must never
    touch -- a different vocabulary than protected_agent_ids() (agent_id), for
    the sites that key off the tmux session directly (scripts/lib/lane_winddown.py,
    scripts/fleet_model.sh). Union of: every registry row's tmux_session (migration
    066) + tmux_session_aliases (migration 067, e.g. cc-orchestrator's 'orch' +
    'orchestrator') + PROTECTED_NON_AGENT_SESSIONS (services with no agent_id at
    all, e.g. 'fleet-console'). Fails safe to _FALLBACK_PROTECTED_SESSIONS (never
    empty, never raises) -- same dead-man rationale as protected_agent_ids()."""
    try:
        sessions = set(PROTECTED_NON_AGENT_SESSIONS)
        for a in protected_agents(dsn):
            if a.tmux_session:
                sessions.add(a.tmux_session)
            sessions.update(a.tmux_session_aliases)
        return frozenset(sessions)
    except Exception:  # noqa: BLE001 — dead-man fallback, must never propagate
        return _FALLBACK_PROTECTED_SESSIONS


def boots_from_env_only(agent_id: str, dsn: Optional[str] = None) -> bool:
    """True for the narrow scripts/lib/lane_token_resolver.py _NO_POINTER_SINGLETONS
    set (currently just 'cai') -- see migration 066's column comment for why this
    is a filter over the registry rather than a second list. NOT YET consumed by
    lane_token_resolver.py itself in this pass (deliberately deferred, see
    reports/substrate-ihsanification-next-moves-op42896.md)."""
    try:
        for a in protected_agents(dsn):
            if a.agent_id == agent_id:
                return a.boots_from_env_only
    except Exception:  # noqa: BLE001
        pass
    return False


def _main() -> int:
    """CLI for bash consumers (scripts/fleet_model.sh) that can't import this
    module directly. `sessions` prints protected_tmux_sessions() space-separated
    on one line -- the exact shape scripts/fleet_model.sh's old CORE_LANES="a b c"
    literal was, for a drop-in `CORE_LANES="$(python3 -m nervous_system.protected_agents sessions)"`.
    protected_tmux_sessions() itself never raises (fails safe internally), so this
    always prints something and exits 0 -- callers should still treat empty output
    or a nonzero exit as "cannot determine the protected set" and fail CLOSED
    (protect everything, wind/flip nothing) rather than proceed with no exclusion
    at all, per orch-console's ruling (bus #43044)."""
    import sys

    if len(sys.argv) != 2 or sys.argv[1] != "sessions":
        print("usage: python -m nervous_system.protected_agents sessions", file=sys.stderr)
        return 2
    print(" ".join(sorted(protected_tmux_sessions())))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_main())
