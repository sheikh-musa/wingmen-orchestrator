"""Tests for scripts/auto_recycle_on_bloat.py — STAGE 0 (detect-only).

The two load-bearing safety gates get PURE unit tests (op#18526 MUSTs A + B):
  gate-4 (tier boundary): FAIL-CLOSED — singletons + unknown bodies are never eligible.
  gate-2 (not-self-compacted): a fresh read below threshold => SKIP; None fails closed.
Plus the Stage-0 classify() decision, incl. the SELF-COMPACTED (gate-2 save) verdict that
Stage-0's whole job is to prove (MUST C). No DB, no tmux — the gates are pure functions.
"""
import pytest

from scripts import auto_recycle_on_bloat as ar
from scripts.context_health_watchdog import AgentCtx

SINGLETONS = frozenset({"cc-orchestrator", "cai", "orch-console", "cc-fleet-health"})
WORKERS = frozenset({"cc-storefront", "cc-irsyad", "cc-finance", "cc-storefront-b"})


# ── _base_agent (sub-tag stripping) ──────────────────────────────────────────
@pytest.mark.parametrize("agent,base", [
    ("cc-irsyad-1", "cc-irsyad"),
    ("cc-storefront-b-1", "cc-storefront-b"),  # lettered sub-family kept; numeric tail stripped
    ("cai", "cai"),
    ("cc-fleet-health", "cc-fleet-health"),
])
def test_base_agent(agent, base):
    assert ar._base_agent(agent) == base


# ── G4 tier eligibility — FAIL-CLOSED (MUST A) ───────────────────────────────
@pytest.mark.parametrize("agent", ["cai", "cc-orchestrator", "orch-console", "cc-fleet-health"])
def test_g4_singletons_never_eligible(agent):
    assert ar.tier_eligible(agent, SINGLETONS, WORKERS) is False


@pytest.mark.parametrize("agent", ["cc-storefront-1", "cc-irsyad-2", "cc-finance-1", "cc-storefront-b-1"])
def test_g4_resolved_worker_lanes_eligible(agent):
    assert ar.tier_eligible(agent, SINGLETONS, WORKERS) is True


@pytest.mark.parametrize("agent", ["cc-unknown-xyz", "cc-newlane-1", "random-thing", "cc-quality-1"])
def test_g4_unknown_body_fails_closed(agent):
    # A base the classifier does not know => treated as singleton => NOT eligible.
    assert ar.tier_eligible(agent, SINGLETONS, WORKERS) is False


def test_g4_empty_worker_bases_makes_nothing_eligible():
    # DB resolve failure returns an empty set -> fail-closed for everyone.
    assert ar.tier_eligible("cc-storefront-1", SINGLETONS, frozenset()) is False


# ── G2 not-self-compacted (MUST B) ───────────────────────────────────────────
def test_g2_self_compacted_below_threshold_skips():
    assert ar.self_compacted(35, threshold=88) is True     # 100%->35% self-heal = SKIP


def test_g2_still_red_does_not_skip():
    assert ar.self_compacted(92, threshold=88) is False


def test_g2_at_threshold_is_not_compacted():
    assert ar.self_compacted(88, threshold=88) is False     # >= threshold


def test_g2_none_fresh_read_fails_closed_skips():
    # Can't confirm it's still bloated at fire-time -> do NOT fire.
    assert ar.self_compacted(None, threshold=88) is True


# ── Stage-0 classify() decision ──────────────────────────────────────────────
def _ctx(agent, pct, stale=False, age_s=10):
    return AgentCtx(agent=agent, ctx_tokens=pct * 10000, pct=pct, level="red",
                    age_s=age_s, action="reset-eligible", note="", stale=stale)


def test_classify_singleton_escalates_never_fires():
    d = ar.classify(_ctx("cai", 92), SINGLETONS, WORKERS, fresh_pct=92, idle=True)
    assert d.verdict == "SINGLETON-ESCALATE"


def test_classify_unknown_body_escalates_fail_closed():
    d = ar.classify(_ctx("cc-unknown-9", 99), SINGLETONS, WORKERS, fresh_pct=99, idle=True)
    assert d.verdict == "SINGLETON-ESCALATE"


def test_classify_self_compacted_is_the_gate2_save():
    # worker lane, was 100% at detect, fresh read 35% -> SKIP (the proof Stage-0 exists for).
    d = ar.classify(_ctx("cc-irsyad-1", 100), SINGLETONS, WORKERS, fresh_pct=35, idle=True)
    assert d.verdict == "SELF-COMPACTED"


def test_classify_stale_reading_gated():
    d = ar.classify(_ctx("cc-storefront-1", 99, stale=True), SINGLETONS, WORKERS, fresh_pct=99, idle=True)
    assert d.verdict == "GATED"


def test_classify_busy_worker_gated_not_fired():
    d = ar.classify(_ctx("cc-storefront-1", 92), SINGLETONS, WORKERS, fresh_pct=92, idle=None)
    assert d.verdict == "GATED"


def test_classify_would_fire_only_when_worker_stillred_idle():
    d = ar.classify(_ctx("cc-storefront-1", 92), SINGLETONS, WORKERS, fresh_pct=92, idle=True)
    assert d.verdict == "WOULD-FIRE"


# ── classify_pane() — the op#13050-A PANE-signal decision (detect-only) ───────
FIRE_K = ar.PANE_FIRE_K


def test_classify_pane_not_bloated_no_hint_is_none_safe():
    # No pane hint (None) => NOT-BLOATED, fail-closed — never probes idle, never fires.
    d = ar.classify_pane("storefront", "cc-storefront", SINGLETONS, WORKERS,
                         bloat_k=None, verdict_state="IDLE_EMPTY")
    assert d.verdict == "NOT-BLOATED"


def test_classify_pane_below_bar_not_bloated():
    d = ar.classify_pane("irsyad", "cc-irsyad", SINGLETONS, WORKERS,
                         bloat_k=FIRE_K - 1, verdict_state="IDLE_EMPTY")
    assert d.verdict == "NOT-BLOATED"


def test_classify_pane_singleton_over_bar_escalates_never_fires():
    d = ar.classify_pane("cai", "cai", SINGLETONS, WORKERS,
                         bloat_k=FIRE_K + 100, verdict_state="IDLE_EMPTY")
    assert d.verdict == "SINGLETON-ESCALATE"


def test_classify_pane_unregistered_session_fails_closed():
    # base=None (session not in fleet_lanes) over the bar => escalate, never fire.
    d = ar.classify_pane("mystery", None, SINGLETONS, WORKERS,
                         bloat_k=FIRE_K + 50, verdict_state="IDLE_EMPTY")
    assert d.verdict == "SINGLETON-ESCALATE"


@pytest.mark.parametrize("state", ["WORKING", "STAGED", "GHOST_WEDGED", "UNSURE", "UNSURE:X"])
def test_classify_pane_bloated_worker_not_idle_is_gated(state):
    d = ar.classify_pane("storefront", "cc-storefront", SINGLETONS, WORKERS,
                         bloat_k=FIRE_K + 30, verdict_state=state)
    assert d.verdict == "GATED"


def test_classify_pane_would_fire_only_worker_bloated_idle():
    d = ar.classify_pane("storefront", "cc-storefront", SINGLETONS, WORKERS,
                         bloat_k=FIRE_K + 30, verdict_state="IDLE_EMPTY")
    assert d.verdict == "WOULD-FIRE"
