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

Fail-safe default: `protected_agent_ids()` / `protected_tmux_sessions()` /
`tmux_session_for()` / `boots_from_env_only()` all fall back to a hardcoded
FALLBACK set on TWO failure modes, not just one — a raised DB error, AND a read
that SUCCEEDS but is missing a core member (`_CORE_REQUIRED_AGENT_IDS`: cai,
cc-orchestrator, orch-console, cc-fleet-health). The second mode was a real gap
found in review (bus #43051, 2026-09-24): a wrong DSN or an RLS role filtering
every row out returns [] without raising, which is just as dangerous as an
unreachable DB but was not caught by an `except Exception` alone. See
`_safe_registry_rows()` — the single gate every accessor routes through. A
transient blip returning "nobody is protected" would be catastrophic; a
stale-but-safe fallback, logged loudly, is the correct fail mode here.
"""
from __future__ import annotations

import os
import sys
import warnings
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

# FAIL-OPEN GUARD (orch-console review, bus #43051, 2026-09-24): a registry read
# that SUCCEEDS but returns too few rows (wrong DSN, an RLS role filtering
# everything out, a truncated migration) is NOT a raised exception -- without
# this check it would silently produce an empty/near-empty protected set, which
# is exactly as dangerous as the DB-unreachable case both accessors already
# guard against, but was NOT caught by the `except Exception` fallback. Any read
# missing one of these agent_ids is treated as untrustworthy, same as an error.
_CORE_REQUIRED_AGENT_IDS = frozenset({
    "cai", "cc-orchestrator", "orch-console", "cc-fleet-health",
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


def _safe_registry_rows(dsn: Optional[str] = None) -> Optional[list[ProtectedAgent]]:
    """The live rows, or None if this read must NOT be trusted — a raised DB
    error, OR a read that succeeded but is missing one of _CORE_REQUIRED_AGENT_IDS
    (bus #43051: a successful-but-empty/partial read is just as dangerous as an
    exception and was NOT previously caught). Every accessor below must treat
    None as "fall back to the static floor", never as "the registry says nobody
    is protected". Logs LOUD (stderr + a warning) on either failure mode so a
    silently-degraded registry doesn't go unnoticed."""
    try:
        rows = protected_agents(dsn)
    except Exception as exc:  # noqa: BLE001 — dead-man fallback, must never propagate
        msg = f"protected_agents: DB read failed ({exc!r}) — falling back to the static floor"
        print(msg, file=sys.stderr)
        warnings.warn(msg, stacklevel=2)
        return None
    ids = {a.agent_id for a in rows}
    missing = _CORE_REQUIRED_AGENT_IDS - ids
    if missing:
        msg = (
            f"protected_agents: registry read is missing core member(s) {sorted(missing)} "
            f"(got {len(rows)} row(s)) — falling back to the static floor rather than "
            f"trust a suspect read"
        )
        print(msg, file=sys.stderr)
        warnings.warn(msg, stacklevel=2)
        return None
    return rows


def protected_agent_ids(dsn: Optional[str] = None) -> frozenset[str]:
    """The set every consumer that just needs membership-testing should use.
    Fails safe (see module docstring) to the hardcoded union fallback on any DB
    error OR an untrustworthy read (see _safe_registry_rows) — never returns an
    empty set, never raises."""
    rows = _safe_registry_rows(dsn)
    if rows is None:
        return _FALLBACK_PROTECTED
    return frozenset(a.agent_id for a in rows)


def tmux_session_for(agent_id: str, dsn: Optional[str] = None) -> Optional[str]:
    """The tmux session name this agent_id boots under, or None if unconfirmed
    OR the registry read itself is untrustworthy (see _safe_registry_rows).
    None must be treated as "unknown", never as "this agent has no session" —
    several agent_ids have a real session but it isn't sourced/confirmed yet
    (see migration 066's column comment)."""
    rows = _safe_registry_rows(dsn)
    if rows is None:
        return None
    for a in rows:
        if a.agent_id == agent_id:
            return a.tmux_session
    return None


def protected_tmux_sessions(dsn: Optional[str] = None) -> frozenset[str]:
    """The set of tmux SESSION NAMES a winddown/model-flip/etc. path must never
    touch -- a different vocabulary than protected_agent_ids() (agent_id), for
    the sites that key off the tmux session directly (scripts/lib/lane_winddown.py,
    scripts/fleet_model.sh). Union of: every registry row's tmux_session (migration
    066) + tmux_session_aliases (migration 067, e.g. cc-orchestrator's 'orch' +
    'orchestrator') + PROTECTED_NON_AGENT_SESSIONS (services with no agent_id at
    all, e.g. 'fleet-console'). Fails safe to _FALLBACK_PROTECTED_SESSIONS on a DB
    error OR an untrustworthy read (see _safe_registry_rows) — never empty, never
    raises."""
    rows = _safe_registry_rows(dsn)
    if rows is None:
        return _FALLBACK_PROTECTED_SESSIONS
    sessions = set(PROTECTED_NON_AGENT_SESSIONS)
    for a in rows:
        if a.tmux_session:
            sessions.add(a.tmux_session)
        sessions.update(a.tmux_session_aliases)
    return frozenset(sessions)


def boots_from_env_only(agent_id: str, dsn: Optional[str] = None) -> bool:
    """True for the narrow scripts/lib/lane_token_resolver.py _NO_POINTER_SINGLETONS
    set (currently just 'cai') -- see migration 066's column comment for why this
    is a filter over the registry rather than a second list. NOT YET consumed by
    lane_token_resolver.py itself in this pass (deliberately deferred, see
    reports/substrate-ihsanification-next-moves-op42896.md). Returns False (the
    already-conservative default) if the registry read is untrustworthy — see
    _safe_registry_rows."""
    rows = _safe_registry_rows(dsn)
    if rows is None:
        return False
    for a in rows:
        if a.agent_id == agent_id:
            return a.boots_from_env_only
    return False


def console_protected_identities(dsn: Optional[str] = None) -> frozenset[str]:
    """The union both fleet-console UIs' lane-action guard needs (op#42896/#42909
    P1): `nervous_system/console/app.py`'s `_LANE_ACTION_PROTECTED` and
    `nervous_system/console/hosted_server.py`'s `_PROTECTED` (documented there,
    verbatim, as "mirrors app.py") were two independently-hand-maintained copies
    of the exact same set -- the module docstring's own motivating example
    ("app.py's own literal set was missing 'cai' before a union with a second
    source papered over it") is literally this site. A lane-action target can be
    named either by tmux session or by agent_id depending on the row, so this is
    `protected_tmux_sessions() | protected_agent_ids()`, plus `"hub"` -- a
    console-only UI alias for cc-orchestrator that is not a real tmux session
    name or agent_id anywhere else in this repo, so it stays a literal here
    rather than leaking a console-specific concept into either registry
    accessor. Fails safe the same way both underlying accessors do -- never
    empty, never raises."""
    return protected_tmux_sessions(dsn) | protected_agent_ids(dsn) | {"hub"}


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
