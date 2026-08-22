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


def test_classify_pane_idle_bar_lowered_to_recycle_before_red_zone():
    # Musa op#15956 / #31751: idle-recycle is FREE, so recycle BEFORE the red zone —
    # "if it has to reach 94% it's broken". The IDLE fire bar drops from ~85% (850k)
    # to ~65% (~650k). Pins the INTENT (absolute, not relative to PANE_FIRE_K):
    #  - a ~70% idle worker (700k) MUST now WOULD-FIRE (it did NOT at the old 850 bar)
    #  - a ~50% idle worker (500k) must NOT fire (don't churn a still-healthy lane)
    # Together these pin PANE_FIRE_K into (500, 700] ~= the ~65% target.
    hot = ar.classify_pane("irsyad", "cc-irsyad", SINGLETONS, WORKERS,
                           bloat_k=700, verdict_state="IDLE_EMPTY")
    assert hot.verdict == "WOULD-FIRE"
    healthy = ar.classify_pane("irsyad", "cc-irsyad", SINGLETONS, WORKERS,
                               bloat_k=500, verdict_state="IDLE_EMPTY")
    assert healthy.verdict == "NOT-BLOATED"


# ── select_escalates() — Musa's invisibility gap (op#28437) ──────────────────
# A SINGLETON-ESCALATE lane (pane >= fire bar but NOT a resolved worker lane — a
# singleton, or a worker missing from fleet_lanes) is NEVER a WOULD-FIRE, so
# warn_console never surfaces it and it climbs to 100% invisibly. select_escalates
# is the PURE picker that pulls exactly those lanes when they are ALSO clean-idle,
# for a DISTINCT deduped WARN. Idle is required (never nag a body still working) and
# WOULD-FIRE is excluded (warn_console owns those — no double-page).
def _w(session, base, k, verdict_state, decision_verdict):
    """Build one observe-pass `watched` tuple: (session, base, k, verdict_state, Decision)."""
    return (session, base, k, verdict_state,
            ar.Decision(base or session, int(k), decision_verdict, ""))


def test_select_escalates_picks_singleton_escalate_idle():
    watched = [_w("s1", None, 900, "IDLE_EMPTY", "SINGLETON-ESCALATE")]
    got = ar.select_escalates(watched)
    assert len(got) == 1 and got[0][0] == "s1"


@pytest.mark.parametrize("state", ["WORKING", "STAGED", "GHOST_WEDGED", "UNSURE", "UNSURE:X"])
def test_select_escalates_excludes_singleton_escalate_when_not_idle(state):
    # Never nag a body that is still working / staged / unsure — idle is required.
    watched = [_w("s1", None, 900, state, "SINGLETON-ESCALATE")]
    assert ar.select_escalates(watched) == []


def test_select_escalates_excludes_would_fire():
    # WOULD-FIRE lanes are warn_console's — the escalate path must not double-page them.
    watched = [_w("s2", "cc-irsyad", 900, "IDLE_EMPTY", "WOULD-FIRE")]
    assert ar.select_escalates(watched) == []


def test_select_escalates_excludes_gated_and_not_bloated():
    watched = [
        _w("s3", "cc-irsyad", 900, "STAGED", "GATED"),
        _w("s4", "cc-irsyad", 400, "IDLE_EMPTY", "NOT-BLOATED"),
    ]
    assert ar.select_escalates(watched) == []


def test_select_escalates_empty_in_empty_out():
    assert ar.select_escalates([]) == []


def test_select_escalates_mixed_picks_only_idle_escalates():
    watched = [
        _w("s1", None, 900, "IDLE_EMPTY", "SINGLETON-ESCALATE"),      # picked
        _w("s2", None, 950, "WORKING", "SINGLETON-ESCALATE"),        # busy -> excluded
        _w("s3", "cc-irsyad", 900, "IDLE_EMPTY", "WOULD-FIRE"),      # warn_console's -> excluded
        _w("s4", "cc-irsyad", 300, "IDLE_EMPTY", "NOT-BLOATED"),     # below bar -> excluded
    ]
    got = ar.select_escalates(watched)
    assert [w[0] for w in got] == ["s1"]
