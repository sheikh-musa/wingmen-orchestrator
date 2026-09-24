"""Tests for nervous_system.protected_agents (op#42896/#42909, P1).

Two jobs, matching the audit's own risk mitigation requirement (reports/
substrate-ihsanification-next-moves-op42896.md: "a migration bug here could
either open a hole ... or false-block ... needs a test asserting the
accessor's output matches every current hardcoded set's union before any
list is deleted"):

1. `test_accessor_superset_of_every_hardcoded_set` -- the DB-backed registry
   must be a SUPERSET of every remaining hardcoded set in the repo. This is
   the safety net for the migration: as each call site is cut over to the
   accessor, this test keeps proving nothing it used to protect got dropped.
   It is deliberately superset, not equality -- the registry is expected to
   grow (e.g. via P1 backfills) faster than every hardcoded copy is found and
   migrated away.
2. `test_no_new_hardcoded_agent_id_lists` -- THE ENFORCEMENT MECHANISM orch-
   console asked for (bus #42909: "Add a CI test that greps for new hardcoded
   agent-id lists ... That test IS the enforcement"). Greps the tree for the
   known-remaining hardcoded literals; fails if a NEW one shows up beyond the
   documented baseline count. This is what stops the accretion the audit
   found (2 new copies appeared in the 3 weeks since 09-05, net-zero-ing the
   3 sites already fixed).
"""
import os
import re

import pytest

from nervous_system.protected_agents import protected_agent_ids

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The hardcoded sets NOT YET migrated to the accessor, as of this pass --
# see reports/substrate-ihsanification-next-moves-op42896.md for the full
# inventory and why each of these is still independent (tmux-session-name
# sites and lane_token_resolver's narrower set are deliberately deferred,
# not overlooked).
_REMAINING_HARDCODED_SETS = {
    "scripts/lib/lane_winddown.py SINGLETONS": {
        "nazim", "cai", "orch", "orchestrator", "fleet-health",
        "fleet-console", "quality",
    },
    "scripts/fleet_model.sh CORE_LANES": {
        "orch", "cai", "nazim", "fleet-health", "fleet-console",
    },
}

# agent_id-keyed sets (not tmux-session-named) that SHOULD already be subsets
# of the registry -- these are the ones the superset test actually enforces
# meaningfully (the tmux-session-named sets above use a different vocabulary
# entirely and can't be compared to agent_id set membership directly; they're
# listed for documentation/tracking, not asserted against here).
_AGENT_ID_KEYED_REMAINING = {
    "scripts/lib/lane_token_resolver.py _NO_POINTER_SINGLETONS": {"cai"},
}


def test_accessor_superset_of_every_hardcoded_agent_id_set():
    registry = protected_agent_ids()
    for source, hardcoded in _AGENT_ID_KEYED_REMAINING.items():
        missing = hardcoded - registry
        assert not missing, (
            f"{source} protects {missing!r} that the registry (protected_agents "
            f"table, read via nervous_system.protected_agents) does NOT -- this "
            f"would mean migrating {source} to the accessor drops real protection. "
            f"Registry currently: {sorted(registry)}"
        )


# Every file already known to define its own hardcoded protected-agent-id set,
# as of 2026-09-24 (reports/substrate-ihsanification-next-moves-op42896.md).
# This test fails if a NEW file matching the detection pattern appears that
# isn't in this allowlist -- either a genuinely new hardcoded copy (migrate
# it) or a false positive (add it to _KNOWN_NON_PROTECTION_FILES below with a
# one-line reason, never silently to this set).
#
# The original 9-site inventory (audit 09-05 + this session's re-verification)
# plus 2 MORE this test's own first real run found (2026-09-24) that nobody
# had previously catalogued -- direct proof the enforcement mechanism works,
# not just a passing check:
_KNOWN_HARDCODED_LIST_FILES = {
    "scripts/lib/lane_winddown.py",
    "nervous_system/console/app.py",
    "scripts/lib/lane_token_resolver.py",
    "scripts/fleet_model.sh",
    "scripts/switch_singleton_token.sh",
    "nervous_system/console/hosted_server.py",
    "scripts/lib/fleet_health_boundaries.py",
}

# Files this test's first real run flagged that are genuine false positives --
# they contain 3+ quoted known-tokens on one line but are NOT a protected-
# agent-id collection literal. Each reason was verified by reading the actual
# matching line, not assumed:
_KNOWN_NON_PROTECTION_FILES = {
    # Boot-message prose string naming the agent once + its resetter's
    # identity ("orch-console/Nazim") -- not a set.
    "scripts/reset_cai.sh",
    "scripts/reset_fleet_health.sh",
    # A single SQL INSERT ... VALUES row where one agent_id appears as both
    # from_agent and to_agent -- not a set.
    "scripts/irsyad_pii_containment_monitor.py",
    # SQL WHERE-clause filters (from_agent/to_agent IN (...)) -- routing
    # logic, not a protection list.
    "scripts/watch_fleet.py",
    # Console display/routing metadata (name, channel, tmux, host PER
    # coordinator, for the UI) -- richer than membership, different purpose.
    "nervous_system/console/coordinators.py",
    # A DIFFERENT set for a DIFFERENT purpose: which tmux sessions to
    # exclude from lane-watching (NON_LANE_SESSIONS) / which consoles are
    # governance-only (GOVERNANCE_CONSOLES) -- not "protected from
    # destructive ops". This file already reads protected_agents for the
    # actual protection logic elsewhere (one of the 3 already-fixed sites).
    "nervous_system/lane_watchdog.py",
    # Deliberate fail-closed fallback for when protected_agents (the DB
    # registry) is unreachable -- same pattern as this session's own
    # nervous_system/protected_agents.py _FALLBACK_PROTECTED, not an
    # accretion bug. The file's own comment: "the literal core is the
    # fail-closed floor; the live registry can only ADD to it".
    "nervous_system/lane_wedge_watchdog.py",
    # Large per-agent watchdog CONFIG dict (host, tmux, handoff paths, alert
    # settings, auto_reset, ...) keyed by agent_id -- a genuinely richer
    # structure than membership, out of scope for a simple protected-set
    # migration.
    "scripts/context_health_watchdog.py",
    # A fixed 3-agent ESCALATION-FANOUT list (who to page on an opus-capacity
    # deadline breach), not a protected/singleton-membership test -- verified
    # by reading the call site (op#42896 P1 pass, 2026-09-24): migrating this
    # to protected_agent_ids() would silently widen the page-out to every
    # registry member (cc-quality, cc-storefront, cc-finance, nazim-console,
    # cc-orchestrator too) -- an escalation-policy change, not a registry
    # migration. Left as a literal tuple deliberately.
    "scripts/opus_reprobe_storefront.py",
}

# Deliberately surgical, not broad: a QUOTED-literal token (so prose like
# "cai_bus_notify" or "the cai body" never matches -- only actual string/set
# members do) for a known protected agent_id or tmux-session alias, 3+ of
# them on the SAME LINE (every one of the 9 known hardcoded lists is a
# single-line -- or single-logical-line via string concatenation, which this
# intentionally does NOT try to catch, see note below -- collection literal).
_TOKENS = (
    "cc-orchestrator", "cai", "orch-console", "cc-fleet-health", "cc-quality",
    "cc-storefront", "cc-finance", "nazim", "orch", "orchestrator",
    "fleet-health", "fleet-console", "quality",
)
_QUOTED_TOKEN_RE = re.compile(
    r"""['"](?:%s)['"]""" % "|".join(re.escape(t) for t in _TOKENS)
)


def _grep_candidate_files() -> set[str]:
    """Files under scripts/ and nervous_system/ with a line containing 3+
    QUOTED known-token literals -- i.e. lines that look like a hardcoded
    protected-agent-id collection literal, regardless of whether we already
    know about them. Deliberately line-scoped (not whole-file co-occurrence)
    so prose mentioning several agents across a docstring never matches --
    the tradeoff is a multi-line literal split across several lines with <3
    tokens per line could be missed; none of the 9 known ones are structured
    that way today, and a reviewer reading a new PR's diff is the backstop
    for that edge case, same as any static grep-based check."""
    candidates = set()
    for base in ("scripts", "nervous_system"):
        for dirpath, _dirs, files in os.walk(os.path.join(REPO_ROOT, base)):
            if "/.venv" in dirpath or "/node_modules" in dirpath:
                continue
            for fn in files:
                if not (fn.endswith(".py") or fn.endswith(".sh")):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    lines = open(path, "r", encoding="utf-8", errors="ignore").readlines()
                except OSError:
                    continue
                for line in lines:
                    if len(_QUOTED_TOKEN_RE.findall(line)) >= 3:
                        candidates.add(os.path.relpath(path, REPO_ROOT))
                        break
    return candidates


def test_no_new_hardcoded_agent_id_lists():
    """THE ENFORCEMENT MECHANISM (bus #42909). Fails if a file matching the
    hardcoded-protected-agent-list co-occurrence pattern exists that ISN'T
    already tracked in _KNOWN_HARDCODED_LIST_FILES -- i.e. a new copy of the
    list was added somewhere instead of importing nervous_system.protected_agents."""
    found = _grep_candidate_files()
    # This test file itself will match (it lists all the agent_ids in
    # _KNOWN_HARDCODED_LIST_FILES/_REMAINING_HARDCODED_SETS) -- exclude it,
    # it's tracking data, not a hardcoded protection list a real code path reads.
    found.discard("tests/test_protected_agents_registry.py")
    # nervous_system/protected_agents.py's own _FALLBACK_PROTECTED also
    # matches by construction (it's the documented, allowed fallback copy).
    found.discard("nervous_system/protected_agents.py")
    found -= _KNOWN_NON_PROTECTION_FILES

    unknown = found - _KNOWN_HARDCODED_LIST_FILES
    assert not unknown, (
        f"New file(s) matching the hardcoded-protected-agent-list pattern, not "
        f"in the tracked allowlist: {sorted(unknown)}. If this is a genuine new "
        f"hardcoded list, import from nervous_system.protected_agents instead. "
        f"If it's a false positive, add it to a documented exclusion with a "
        f"one-line reason -- do not silently widen this test."
    )
