"""Parity tests for the singleton/protected-agent PER-NODE RECIPE maps
(backlog#65 cp#91, orch-console ruling bus #46247, 2026-09-30).

These maps are NOT membership-set duplicates of nervous_system.protected_agents
(that migration is tracked separately by tests/test_protected_agents_registry.py
-- see its _KNOWN_NON_PROTECTION_FILES entries for lane_wedge_watchdog.py and
switch_singleton_token.sh, which document exactly why). They are a DIFFERENT
data shape: per-node metadata (a wedge-recovery spec, a re-token boot recipe)
keyed by an agent_id that must ALSO be a real protected singleton. The risk
these guard against is drift, not duplication: a recipe naming an agent_id the
registry doesn't know about (stale/typo'd), or a monitored singleton silently
missing its recovery recipe. Ruling: "their keys subset the protected_agents
singletons (and every protected singleton that needs a recovery recipe has
one). A new singleton then fails CI instead of silently lacking a recipe."
"""
import os
import re

from nervous_system.lane_wedge_watchdog import MONITOR_SINGLETONS, _SINGLETONS
from nervous_system.protected_agents import protected_agent_ids, protected_tmux_sessions
from scripts.lib.lane_token_resolver import _NO_POINTER_SINGLETONS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── lane_wedge_watchdog.py: MONITOR_SINGLETONS <-> _SINGLETONS <-> registry ───

def test_monitor_singletons_is_a_subset_of_the_protected_registry():
    """Every Signal-A-monitored singleton must be a real protected agent -- a
    stale/typo'd env override naming an agent the registry doesn't know would
    otherwise silently monitor (and try to recover) nothing real."""
    registry = protected_agent_ids()
    unknown = set(MONITOR_SINGLETONS) - registry
    assert not unknown, (
        f"MONITOR_SINGLETONS names {unknown!r}, which is NOT in "
        f"protected_agent_ids() ({sorted(registry)}) -- fix the env override or "
        f"the registry."
    )


def test_every_monitored_singleton_has_a_recovery_recipe():
    """Every name in MONITOR_SINGLETONS must have a _SINGLETONS entry -- the
    watchdog looks up `_SINGLETONS.get(obs.agent, {})` for its recovery
    'kind'/nudge/label; a monitored-but-recipe-less singleton would silently
    fall through to the empty-dict default instead of failing loudly."""
    missing = set(MONITOR_SINGLETONS) - set(_SINGLETONS)
    assert not missing, (
        f"{missing!r} is in MONITOR_SINGLETONS but has no _SINGLETONS recovery "
        f"recipe -- add one (kind/nudge/label) before monitoring it."
    )


def test_every_recovery_recipe_names_a_real_protected_singleton():
    """The reverse direction: a _SINGLETONS entry for an agent_id that isn't (or
    is no longer) a protected singleton is dead/stale config."""
    registry = protected_agent_ids()
    unknown = set(_SINGLETONS) - registry
    assert not unknown, (
        f"_SINGLETONS has recovery recipe(s) for {unknown!r}, which is NOT in "
        f"protected_agent_ids() ({sorted(registry)}) -- stale entry, or the "
        f"registry lost a row it shouldn't have."
    )


# ── switch_singleton_token.sh: NODE REGISTRY case-statement labels ───────────

def _switch_singleton_node_registry_labels() -> set[str]:
    """Extract the bare case-statement labels between `case "$NODE" in` and its
    matching `esac` -- i.e. the node names switch_singleton_token.sh knows how
    to re-token. Deliberately excludes the `*)` fallthrough (not a real node)."""
    path = os.path.join(REPO_ROOT, "scripts", "switch_singleton_token.sh")
    text = open(path, "r", encoding="utf-8").read()
    m = re.search(r'case "\$NODE" in(.*?)\nesac', text, re.DOTALL)
    assert m, "NODE REGISTRY case block not found -- did switch_singleton_token.sh change shape?"
    block = m.group(1)
    return {
        label for label in re.findall(r'^\s*([A-Za-z0-9_-]+)\)\s*$', block, re.MULTILINE)
        if label != "*"
    }


def test_switch_singleton_node_registry_is_a_subset_of_the_protected_registry():
    """Every node switch_singleton_token.sh can re-token must be a real
    protected singleton -- a stale label (e.g. a renamed/retired agent_id)
    would otherwise silently dispatch a re-token onto a boot recipe for a body
    that no longer exists as such."""
    labels = _switch_singleton_node_registry_labels()
    assert labels, "expected at least one NODE REGISTRY case label"
    registry = protected_agent_ids()
    unknown = labels - registry
    assert not unknown, (
        f"switch_singleton_token.sh's NODE REGISTRY names {unknown!r}, which is "
        f"NOT in protected_agent_ids() ({sorted(registry)})."
    )


# ── lane_token_resolver.py: _NO_POINTER_SINGLETONS (existing documented       ─
#    exception, orch-console ruling item 3: "include it in the same subset    ─
#    test") ─────────────────────────────────────────────────────────────────

def test_no_pointer_singletons_is_a_subset_of_the_protected_registry():
    """_NO_POINTER_SINGLETONS is session-vocabulary ('cai', 'fleet-health'), so
    it subsets protected_tmux_sessions(), not protected_agent_ids()."""
    sessions = protected_tmux_sessions()
    unknown = set(_NO_POINTER_SINGLETONS) - sessions
    assert not unknown, (
        f"lane_token_resolver._NO_POINTER_SINGLETONS names {unknown!r}, which "
        f"is NOT in protected_tmux_sessions() ({sorted(sessions)})."
    )
