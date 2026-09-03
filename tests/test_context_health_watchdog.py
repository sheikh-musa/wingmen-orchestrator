"""Tests for scripts.context_health_watchdog — the ARMED reset executor + gates.

Covers the safety-critical decision logic that auto-manipulates LIVE singleton
agents via cross-host tmux (op#5516 follow-up). We mock the pane-capture / tmux /
handoff-freshness boundaries so nothing here touches a real agent.

Explicitly NOT tested with --arm against a live body — every side-effecting call
(_capture_pane, _send_literal, _send_key, _newest_handoff, _page_loud) is patched.
That is no longer left to memory: tests/conftest.py installs an autouse fixture
that makes each of those seams RAISE, so a test which forgets fails loudly instead
of driving tmux on a live agent or paging the operator (2026-07-26: one such
forgotten patch sent a human two Telegram pages claiming the hub had been cleared,
and the suite stayed green). Patching a seam in a test IS the opt-in.

PaneState is constructed with KEYWORD arguments throughout, deliberately: when
`bg_agents` was inserted ahead of `raw`, every positional construction here
silently bound the pane text to `bg_agents` and left `raw` empty, and nothing
failed. PaneState.__post_init__ now type-asserts, so that class of mistake is
immediate and loud — but keywords keep it from arising at all.
"""
from __future__ import annotations

import json
import time
import types

import pytest

from scripts import context_health_watchdog as w


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #

def _ctx(agent="cc-orchestrator", pct=85, level="red", action="reset-eligible"):
    return w.AgentCtx(agent=agent, ctx_tokens=int(pct / 100 * 1_000_000),
                      pct=pct, level=level, age_s=30, action=action)


# A generic REMOTE body for executor/pane-state unit tests, DECOUPLED from the real registry: host
# is a test placeholder (NOT the dead mac-studio, NOT cross_host_unreachable) so hub-host churn
# (VPS->gzb) never ripples into these transport-patched tests. (Was w._AGENT_REGISTRY["cc-orchestrator"],
# but the hub is now cross_host_unreachable -> _pane_state would short-circuit and break these.)
REG = {"host": "test-host", "tmux": "orch", "handoff_glob": "reports/session-handoff-*.md",
       "handoff_dir": "~/wingmen/orchestrator", "window": 1_000_000, "alerts": True,
       "auto_reset": True, "external_recycle": True, "inbox_scope": "hub", "label": "test hub (remote)"}

# Realistic pane fragments.
PANE_IDLE_AUTHED = (
    "> resumed session\n"
    "╭──────────────────────────────────────────────╮\n"
    "│ >                                             │\n"
    "╰──────────────────────────────────────────────╯\n"
    "  ? for shortcuts\n"
)
PANE_BUSY = (
    "● Running a long task...\n"
    "  ⎿  working\n"
    "  esc to interrupt\n"
)
PANE_LOGIN = (
    "  Welcome to Claude Code\n"
    "  Select login method:\n"
    "  1. Claude account\n"
)
PANE_INPUT_TEXT = (
    "╭──────────────────────────────────────────────╮\n"
    "│ > please deploy the storefront fix now        │\n"
    "╰──────────────────────────────────────────────╯\n"
    "  ? for shortcuts\n"
)
# The CURRENT Claude Code TUI (what cai/orch actually show cross-host): a `❯`
# prompt between horizontal rules + a "⏵⏵ bypass permissions … ← for agents"
# footer. It has NO "│ >" box and NO "? for shortcuts" — the OLD markers miss it.
PANE_IDLE_AUTHED_CURRENT_TUI = (
    "  ⏺ done with the last task\n"
    "────────────────────────────────────────────────────────────────\n"
    "❯                                                               \n"
    "────────────────────────────────────────────────────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n"
)


# --------------------------------------------------------------------------- #
# Reset-confirmation fixtures. These are the REAL numbers from the genuine hub
# reset at 2026-07-26T02:55Z — the empirical basis for "a reset collapses the
# context": session adc34035-… @ 802,287 tokens became f58a2fb4-… @ 91,270.
# (session_id, latest_context_tokens, observed_epoch, db_now_epoch)
# --------------------------------------------------------------------------- #
FP_BEFORE = ("adc34035-90ee-40a4-ae2c-90186bce04f0", 802_287, 1000.0, 1000.0)
FP_AFTER_RESET = ("f58a2fb4-7dd7-45ae-88c1-e26df37c1e68", 91_270, 1015.0, 1020.0)
# Same session, and the row was WRITTEN AFTER the /clear (observed 1015 > the
# db_now 1010 of the first post-clear poll) -> proof the session did not restart.
FP_STILL_SAME_PRE = ("adc34035-90ee-40a4-ae2c-90186bce04f0", 802_287, 1000.0, 1010.0)
FP_STILL_SAME_POST = ("adc34035-90ee-40a4-ae2c-90186bce04f0", 806_400, 1015.0, 1020.0)


def _fingerprints(monkeypatch, *readings):
    """Script _session_fingerprint's return values; the last reading repeats.

    Explicit opt-in to the (conftest-forbidden) telemetry seam — no test here ever
    reads real cc_session_costs."""
    box = list(readings)

    def _fake(agent):
        return box.pop(0) if len(box) > 1 else box[0]

    monkeypatch.setattr(w, "_session_fingerprint", _fake)


@pytest.fixture(autouse=True)
def _default_no_owed_action(monkeypatch):
    """Default seam state for the CAI-500 owed-action gate (cond 1): operator
    inbox drained + no open in-flight executor. Keeps the pre-existing _do_reset
    tests exercising the /clear sequence; the owed-action / capture tests override
    these two seams explicitly. Nothing here touches a real DB."""
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: 0)


# --------------------------------------------------------------------------- #
# idle vs busy detection
# --------------------------------------------------------------------------- #

def test_idle_detection_idle(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_IDLE_AUTHED)
    st = w._pane_state(REG)
    assert st.reachable is True
    assert st.idle is True


def test_idle_detection_busy(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_BUSY)
    st = w._pane_state(REG)
    assert st.idle is False  # "esc to interrupt" -> mid-task -> never reset


def test_pane_unreachable(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: None)
    st = w._pane_state(REG)
    assert st.reachable is False
    assert st.idle is None
    assert st.authenticated is None


# --------------------------------------------------------------------------- #
# authentication detection
# --------------------------------------------------------------------------- #

def test_auth_ok_normal_prompt(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_IDLE_AUTHED)
    assert w._pane_state(REG).authenticated is True


def test_auth_ok_busy_is_still_authed(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_BUSY)
    assert w._pane_state(REG).authenticated is True  # a working session is authed


def test_auth_bad_login_screen(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_LOGIN)
    assert w._pane_state(REG).authenticated is False  # login/model-picker -> NOT safe


def test_auth_unknown_unrecognised_screen(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "some random garbage\nno prompt here\n")
    assert w._pane_state(REG).authenticated is None  # unsure -> caller treats as not-safe


def test_auth_ok_current_tui_prompt(monkeypatch):
    # REGRESSION: the current Claude Code TUI (❯ prompt + horizontal rules +
    # "⏵⏵ bypass permissions … ← for agents" footer) must read as authenticated.
    # The old markers (│ >, ? for shortcuts) missed it -> authed=None -> the
    # watchdog SKIPPED even the safe checkpoint for cai/orch (op#6179).
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_IDLE_AUTHED_CURRENT_TUI)
    st = w._pane_state(REG)
    assert st.authenticated is True
    assert st.idle is True


def test_auth_current_tui_login_still_unsafe(monkeypatch):
    # A login/error screen must STILL read False even if a stray footer word
    # appears — the unauth markers take precedence.
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40:
                        "  Select login method:\n  1. Claude account\n  ← for agents\n")
    assert w._pane_state(REG).authenticated is False


# --------------------------------------------------------------------------- #
# input-box unsent text detection + preservation
# --------------------------------------------------------------------------- #

def test_input_box_text_extracted(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_INPUT_TEXT)
    st = w._pane_state(REG)
    assert "deploy the storefront fix" in st.input_text


def test_input_box_empty_prompt(monkeypatch):
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_IDLE_AUTHED)
    assert w._pane_state(REG).input_text == ""


def test_input_box_placeholder_ignored():
    pane = "│ > Try edit a file to get started              │\n  ? for shortcuts\n"
    assert w._extract_input_text(pane) == ""  # dimmed placeholder, not real input


def test_preserve_input_box_clears_and_folds(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(w, "_send_key", lambda reg, key: sent.append(key) or True)
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    st = w.PaneState(reachable=True, idle=True, authenticated=True,
                     input_text="deploy the fix now")
    folded = w._preserve_input_box(REG, st)
    assert "deploy the fix now" in folded
    assert "C-u" in sent  # box was cleared, never silently clobbered


# --------------------------------------------------------------------------- #
# handoff-freshness gate
# --------------------------------------------------------------------------- #

def test_fresh_handoff_recent(monkeypatch):
    monkeypatch.setattr(w, "_newest_handoff",
                        lambda reg: ("session-handoff-now.md", time.time() - 60))
    assert w._fresh_handoff(REG) == "session-handoff-now.md"


def test_fresh_handoff_stale(monkeypatch):
    old = time.time() - (w._HANDOFF_MAX_AGE_MIN * 60 + 600)  # older than the window
    monkeypatch.setattr(w, "_newest_handoff", lambda reg: ("session-handoff-old.md", old))
    assert w._fresh_handoff(REG) is None


def test_fresh_handoff_none(monkeypatch):
    monkeypatch.setattr(w, "_newest_handoff", lambda reg: None)
    assert w._fresh_handoff(REG) is None


# --------------------------------------------------------------------------- #
# nudge sanitization (no apostrophes/quotes/parens — they break send-keys)
# --------------------------------------------------------------------------- #

def test_sanitize_strips_forbidden_chars():
    dirty = "don't (really) \"do\" this; rm -rf | & `x`"
    clean = w._sanitize_nudge(dirty)
    for bad in "'\"()`$;|&":
        assert bad not in clean


def test_checkpoint_nudge_is_sendkeys_safe():
    nudge = w._checkpoint_nudge(_ctx(), REG, folded="operator's (urgent) note")
    for bad in "'\"()`$;|&\n":
        assert bad not in nudge


def test_boot_nudge_is_sendkeys_safe():
    for bad in "'\"()`$;|&\n":
        assert bad not in w._boot_nudge(REG)


# --------------------------------------------------------------------------- #
# staged decision: amber -> checkpoint-only vs red -> full-reset
# --------------------------------------------------------------------------- #

def test_run_executor_amber_checkpoints_only(monkeypatch, tmp_path):
    calls = {"checkpoint": 0, "reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)  # force a checkpoint
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "fresh handoff x.md"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK"))[1])

    res = w.run_executor([_ctx(pct=70, level="amber", action="checkpoint-nudge")])
    assert calls["checkpoint"] == 1
    assert calls["reset"] == 0  # amber NEVER triggers a reset
    assert "amber checkpoint OK" in res[0]


def test_run_executor_red_full_reset(monkeypatch, tmp_path):
    calls = {"checkpoint": 0, "reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "x"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK (handoff x)"))[1])

    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")])
    assert calls["reset"] == 1
    assert "red reset CONFIRMED" in res[0]


# --------------------------------------------------------------------------- #
# GRANULAR ARMING (CAI-RESP-500): amber = write-only checkpoint half; red = also
# the destructive /clear. The safety-critical invariant is that arm=amber can
# NEVER reach _do_reset / a tmux /clear, even for a RED body.
# --------------------------------------------------------------------------- #

def test_arm_amber_red_body_stays_dry_run_no_reset(monkeypatch, tmp_path):
    """arm=amber on a RED body: checkpoint-only (write-only), NEVER _do_reset."""
    calls = {"checkpoint": 0, "reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)  # force a checkpoint attempt
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "fresh handoff x.md"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK"))[1])

    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="amber")
    assert calls["reset"] == 0        # THE invariant: amber NEVER /clear-resets a red body
    assert calls["checkpoint"] == 1   # but it DOES write-only checkpoint it (≥SOFT)
    assert "WOULD reset" in res[0] and "UNARMED" in res[0]


def test_arm_amber_amber_body_checkpoints(monkeypatch, tmp_path):
    """arm=amber on an amber body: DOES invoke the checkpoint path, never reset."""
    calls = {"checkpoint": 0, "reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "fresh handoff x.md"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK"))[1])

    res = w.run_executor([_ctx(pct=70, level="amber", action="checkpoint-nudge")], arm_level="amber")
    assert calls["checkpoint"] == 1
    assert calls["reset"] == 0
    assert "amber checkpoint OK" in res[0]


def test_arm_red_red_body_resets(monkeypatch, tmp_path):
    """arm=red on a RED body: the destructive reset path IS reachable."""
    calls = {"reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK (handoff x)"))[1])
    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="red")
    assert calls["reset"] == 1
    assert "red reset CONFIRMED" in res[0]


def test_arm_off_is_a_noop(monkeypatch, tmp_path):
    """arm=off never executes anything, even for a red body."""
    calls = {"n": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_reset", lambda a, reg, outcome=None: (calls.__setitem__("n", calls["n"] + 1), (True, "x"))[1])
    monkeypatch.setattr(w, "_do_checkpoint", lambda a, reg, st: (calls.__setitem__("n", calls["n"] + 1), (True, "x"))[1])
    assert w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="off") == []
    assert calls["n"] == 0


def test_plan_arm_amber_red_body_dry_run(monkeypatch):
    """The PRINTED plan for a red body under arm=amber is a dry-run 'WOULD reset',
    and it does NOT probe the live pane (off/amber never touches the agent)."""
    monkeypatch.setattr(w, "_agent_is_idle",
                        lambda reg: pytest.fail("planner probed a live pane under arm=amber"))
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: pytest.fail("planner executed a reset under arm=amber"))
    plan = w.plan_reset(_ctx(pct=88, level="red", action="reset-eligible"), arm_level="amber")
    assert "WOULD reset" in plan and "UNARMED" in plan


def test_plan_arm_amber_amber_body_will_checkpoint(monkeypatch):
    plan = w.plan_reset(_ctx(pct=70, level="amber", action="checkpoint-nudge"), arm_level="amber")
    assert plan.startswith("checkpoint-nudge")  # no 'WOULD' — it will fire


def test_resolve_arm_level_flag_and_env(monkeypatch):
    import argparse
    # flag wins
    ns = argparse.Namespace(arm="amber")
    monkeypatch.setenv("CTX_WD_ARM", "red")
    assert w._resolve_arm_level(ns) == "amber"
    # no flag -> env
    ns2 = argparse.Namespace(arm=None)
    monkeypatch.setenv("CTX_WD_ARM", "amber")
    assert w._resolve_arm_level(ns2) == "amber"
    # no flag, no/invalid env -> off
    ns3 = argparse.Namespace(arm=None)
    monkeypatch.delenv("CTX_WD_ARM", raising=False)
    assert w._resolve_arm_level(ns3) == "off"


def test_run_executor_never_touches_self(monkeypatch, tmp_path):
    """orch-console (self, Mini, self-compacting) must NEVER be auto-reset."""
    calls = {"n": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("n", calls["n"] + 1), (True, "x"))[1])
    monkeypatch.setattr(w, "_do_checkpoint", lambda a, reg, st: (True, "x"))

    res = w.run_executor([_ctx(agent="orch-console", pct=90, level="red", action="reset-eligible")])
    assert calls["n"] == 0  # auto_reset=False -> skipped entirely
    assert res == []


def test_run_executor_red_dedup(monkeypatch, tmp_path):
    """A body reset last cycle is not reset again while the dedup window holds."""
    calls = {"reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("reset", calls["reset"] + 1), (True, "x"))[1])
    # seed state: just reset
    w._save_exec_state({"cc-orchestrator": {"reset_at": time.time()}})
    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")])
    assert calls["reset"] == 0
    assert "deduped" in res[0]


def test_run_executor_deadman_on_crash(monkeypatch, tmp_path):
    """A crash mid-action pages loudly instead of dying silent."""
    paged = []
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))

    def _boom(a, reg, outcome=None):
        raise RuntimeError("tmux vanished")
    monkeypatch.setattr(w, "_do_reset", _boom)

    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")])
    assert paged and "CRASHED" in paged[0]
    assert "EXCEPTION" in res[0]


# --------------------------------------------------------------------------- #
# reset gates: never reset a busy / unauthenticated body
# --------------------------------------------------------------------------- #

def test_do_reset_skips_busy(monkeypatch):
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=False, authenticated=True, input_text="", raw=""))
    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "not idle" in detail


def test_do_reset_skips_unauthenticated(monkeypatch):
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=False, input_text="", raw=""))
    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "authenticated" in detail


def test_do_reset_aborts_when_checkpoint_fails(monkeypatch):
    """No fresh handoff + checkpoint can't produce one -> ABORT, never /clear."""
    paged = []
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)
    monkeypatch.setattr(w, "_do_checkpoint", lambda a, reg, st: (False, "no handoff"))
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))
    cleared = []
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: cleared.append(t) or True)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "ABORT" in detail
    assert "/clear" not in cleared  # never cleared without a saved handoff
    assert paged and "ABORTED" in paged[0]


def test_do_reset_phantom_guard(monkeypatch):
    """If /clear did not land in the box, Enter is NOT pressed."""
    paged, keys = [], []
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: keys.append(k) or True)
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "empty prompt, no clear typed\n")
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    _fingerprints(monkeypatch, FP_BEFORE)  # baseline is taken before the /clear

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "phantom-guard" in detail
    assert "Enter" not in keys  # never blind-submit
    assert paged and "phantom-guard FAILED" in paged[0]


def test_do_reset_full_happy_path(monkeypatch):
    """idle+authed+fresh-handoff -> /clear lands -> auth holds -> boot sent ->
    telemetry PROVES a new, collapsed session -> OK."""
    keys, literals = [], []
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="",
                                                 raw="reading boot_briefing"))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: literals.append(t) or True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: keys.append(k) or True)
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "│ > /clear │\n")  # /clear present
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    _fingerprints(monkeypatch, FP_BEFORE, FP_AFTER_RESET)

    outcome = {}
    ok, detail = w._do_reset(_ctx(), REG, outcome)
    assert ok is True and "reset CONFIRMED" in detail
    assert outcome["confirmation"] == w.CONFIRM_RESET
    assert "/clear" in literals
    assert keys.count("Enter") >= 2  # /clear submit + boot submit


def test_do_reset_auth_broke_after_clear(monkeypatch):
    """If auth breaks after /clear, page LOUDLY and stop (no boot)."""
    paged = []
    states = iter([
        w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""),   # initial gate
        w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""),   # re-verify before clear
        w.PaneState(reachable=True, idle=True, authenticated=False, input_text="",
                    raw="Select login method"),  # after clear: auth broke
    ])
    monkeypatch.setattr(w, "_pane_state", lambda reg: next(states))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "│ > /clear │\n")
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    _fingerprints(monkeypatch, FP_BEFORE)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "auth broke" in detail
    assert paged and "auth BROKE" in paged[0]
    # The page describes a SUBMITTED /clear, never a completed one.
    assert "cleared OK" not in paged[0]


# --------------------------------------------------------------------------- #
# checkpoint executor
# --------------------------------------------------------------------------- #

def test_do_checkpoint_verifies_fresh_handoff(monkeypatch):
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)
    monkeypatch.setattr(w, "_preserve_input_box", lambda reg, st: "")
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    # before: none; after nudge: a brand-new handoff appears
    seq = iter([None, ("session-handoff-new.md", time.time())])
    monkeypatch.setattr(w, "_newest_handoff", lambda reg: next(seq))
    st = w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw="")
    ok, detail = w._do_checkpoint(_ctx(), REG, st)
    assert ok is True and "session-handoff-new.md" in detail


def test_do_checkpoint_times_out_no_handoff(monkeypatch):
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)
    monkeypatch.setattr(w, "_preserve_input_box", lambda reg, st: "")
    monkeypatch.setattr(w, "_newest_handoff", lambda reg: None)  # never appears
    monkeypatch.setattr(w, "_CHECKPOINT_WAIT_S", 0)  # no waiting
    st = w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw="")
    ok, detail = w._do_checkpoint(_ctx(), REG, st)
    assert ok is False and "no fresh handoff" in detail


# --------------------------------------------------------------------------- #
# planner / DRY-RUN: emits the right PLAN without executing
# --------------------------------------------------------------------------- #

def test_plan_dry_run_red_no_execution(monkeypatch):
    """DRY-RUN (armed=False) must NOT probe or execute — pure string plan."""
    monkeypatch.setattr(w, "_agent_is_idle",
                        lambda reg: pytest.fail("planner probed a live pane in DRY-RUN"))
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: pytest.fail("planner executed in DRY-RUN"))
    plan = w.plan_reset(_ctx(pct=88, level="red", action="reset-eligible"), armed=False)
    assert plan.startswith("DRY-RUN")
    assert "full-reset" in plan


def test_plan_dry_run_amber(monkeypatch):
    plan = w.plan_reset(_ctx(pct=70, level="amber", action="checkpoint-nudge"), armed=False)
    assert plan.startswith("WOULD checkpoint-nudge")


def test_plan_self_body_detect_only():
    plan = w.plan_reset(_ctx(agent="orch-console", pct=90, level="red", action="reset-eligible"),
                        armed=False)
    assert "detect-only" in plan and "never auto-reset" in plan


def test_plan_armed_reset_ready(monkeypatch):
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: True)
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    plan = w.plan_reset(_ctx(pct=88, level="red", action="reset-eligible"), armed=True)
    assert "RESET-READY" in plan


def test_plan_armed_gate_fail(monkeypatch):
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: False)  # busy
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)
    plan = w.plan_reset(_ctx(pct=88, level="red", action="reset-eligible"), armed=True)
    assert "NOT-IDLE" in plan


# --------------------------------------------------------------------------- #
# CAI-500 condition 1 — idle = NO OWED ACTION in flight. A body is red-reset-
# eligible ONLY when it has nothing owed: operator inbox drained AND no open
# in-flight executor AND pane idle. Any owed action -> DEFER, never /clear.
# --------------------------------------------------------------------------- #

def test_owed_action_none_when_fully_quiescent(monkeypatch):
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: 0)
    st = w.PaneState(reachable=True, idle=True, authenticated=True)
    assert w._owed_action_in_flight(_ctx(), REG, st) is None


def test_owed_action_defers_on_unhandled_operator_msg(monkeypatch):
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 2)
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: 0)
    st = w.PaneState(reachable=True, idle=True, authenticated=True)
    reason = w._owed_action_in_flight(_ctx(), REG, st)
    assert reason and "operator message" in reason  # inbox not drained -> owed


def test_owed_action_defers_on_open_executor(monkeypatch):
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: 1)
    st = w.PaneState(reachable=True, idle=True, authenticated=True)
    reason = w._owed_action_in_flight(_ctx(), REG, st)
    assert reason and "executor" in reason


def test_owed_action_defers_on_indeterminate_inbox(monkeypatch):
    """Fail-safe: if the inbox drain is UNPROVABLE (DB down / undeclared scope)
    the body counts as owed — we never /clear what we cannot prove is drained."""
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: None)
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: 0)
    st = w.PaneState(reachable=True, idle=True, authenticated=True)
    reason = w._owed_action_in_flight(_ctx(), REG, st)
    assert reason and "UNPROVABLE" in reason


def test_owed_action_defers_on_indeterminate_executor(monkeypatch):
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: None)
    st = w.PaneState(reachable=True, idle=True, authenticated=True)
    reason = w._owed_action_in_flight(_ctx(), REG, st)
    assert reason and "UNPROVABLE" in reason


def test_owed_action_defers_when_not_idle(monkeypatch):
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: 0)
    st = w.PaneState(reachable=True, idle=False, authenticated=True)
    reason = w._owed_action_in_flight(_ctx(), REG, st)
    assert reason and "not idle" in reason


def test_do_reset_defers_when_owed_action(monkeypatch):
    """End-to-end: an owed operator message -> _do_reset DEFERS, NEVER /clear."""
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 3)  # inbox NOT drained
    monkeypatch.setattr(w, "_open_executor_count", lambda a, reg: 0)
    cleared = []
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: cleared.append(t) or True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "DEFER" in detail
    assert "/clear" not in cleared  # owed action -> never cleared


# --------------------------------------------------------------------------- #
# CAI-500 condition 2 — PROVABLE capture before the irreversible /clear. If the
# capture of un-drained input cannot be VERIFIED, ABORT — never /clear.
# --------------------------------------------------------------------------- #

def test_verify_capture_ok_when_saved_and_drained(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    st = w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw="")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is True and "verified" in detail


def test_verify_capture_fails_no_handoff(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)
    st = w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw="")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False and "handoff" in detail  # state NOT saved -> refuse


def test_verify_capture_fails_inbox_not_drained(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 1)  # arrived mid-checkpoint
    st = w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw="")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False and "drained" in detail


def test_verify_capture_fails_indeterminate_inbox(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: None)  # can't re-verify
    st = w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw="")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False


def test_verify_capture_fails_unsent_input_present(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    st = w.PaneState(reachable=True, idle=True, authenticated=True,
                     input_text="still typing this", raw="")  # unsent text remains
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False and "input-box" in detail


def test_do_reset_aborts_when_capture_unverified(monkeypatch):
    """If capture cannot be VERIFIED before /clear -> ABORT loudly, never /clear."""
    paged, cleared = [], []
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_verify_capture_before_clear",
                        lambda reg, st: (False, "inbox re-check unprovable"))
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: cleared.append(t) or True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "ABORT" in detail and "capture" in detail
    assert "/clear" not in cleared            # dead-man's-switch: body left SAFE
    assert paged and "ABORTED" in paged[0]    # loud page


# --------------------------------------------------------------------------- #
# CAI-500 condition 4 — NEVER-SELF, enforced at the executor boundary too (not
# only by the run_executor registry filter).
# --------------------------------------------------------------------------- #

def test_do_reset_refuses_self():
    """A never-self / detect-only body (auto_reset=False) is refused even on a
    DIRECT _do_reset call — the never-self invariant holds at the executor edge."""
    self_reg = w._AGENT_REGISTRY["orch-console"]
    ok, detail = w._do_reset(_ctx(agent="orch-console"), self_reg)
    assert ok is False and "never-self" in detail


# --------------------------------------------------------------------------- #
# RESET CONFIRMATION — "we typed /clear" is not "it cleared".
#
# The 2026-07-26 incident: the operator was paged twice with "cc-orchestrator
# cleared + boot nudge sent" for a reset that never happened. The success claim
# rested on send-keys exit codes and a pane that was reachable + authenticated —
# all of which are equally true of a body nobody touched. These tests pin the only
# evidence that can tell the difference (a NEW cc_session_costs session_id with a
# collapsed token count) and, just as importantly, pin the THREE distinct outcomes
# so measurement lag can never be reported as either success or failure.
# --------------------------------------------------------------------------- #

def test_confirm_reset_confirmed_on_new_collapsed_session(monkeypatch):
    """The real 02:55Z signature: new session, context collapsed to ~11%."""
    _fingerprints(monkeypatch, FP_AFTER_RESET)
    state, why = w._confirm_reset("cc-orchestrator", FP_BEFORE, wait_s=0, poll_s=0)
    assert state == w.CONFIRM_RESET
    assert "f58a2fb4" in why and "collapsed" in why


def test_confirm_reset_new_session_without_collapse_is_not_confirmed(monkeypatch):
    """A different session_id ALONE is not a /clear signature — if the context did
    not collapse, we do not get to say 'cleared'."""
    not_collapsed = (FP_AFTER_RESET[0], 790_000, 1015.0, 1020.0)
    _fingerprints(monkeypatch, not_collapsed)
    state, why = w._confirm_reset("cc-orchestrator", FP_BEFORE, wait_s=0, poll_s=0)
    assert state == w.CONFIRM_UNKNOWN
    assert "did NOT collapse" in why


def test_confirm_reset_refuted_by_post_clear_telemetry(monkeypatch):
    """PROOF of failure: the writer produced a row AFTER our /clear and it is still
    the same session. That is 'confirmed NOT reset', not 'unknown'."""
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    _fingerprints(monkeypatch, FP_STILL_SAME_PRE, FP_STILL_SAME_POST)
    state, why = w._confirm_reset("cc-orchestrator", FP_BEFORE, wait_s=60, poll_s=0)
    assert state == w.CONFIRM_NOT_RESET
    assert "did not restart" in why


def test_confirm_reset_unknown_when_telemetry_has_not_refreshed(monkeypatch):
    """The writer runs every ~300s. Silence inside our 90s window is LAG, and lag
    is neither success nor failure — it is its own answer."""
    _fingerprints(monkeypatch, FP_STILL_SAME_PRE)
    state, why = w._confirm_reset("cc-orchestrator", FP_BEFORE, wait_s=0, poll_s=0)
    assert state == w.CONFIRM_UNKNOWN
    assert "no post-/clear telemetry yet" in why
    assert "may be measurement lag" in why  # the operator is told WHY it is unknown


def test_confirm_reset_unknown_when_db_unreadable(monkeypatch):
    _fingerprints(monkeypatch, None)
    state, why = w._confirm_reset("cc-orchestrator", FP_BEFORE, wait_s=0, poll_s=0)
    assert state == w.CONFIRM_UNKNOWN
    assert "unreadable" in why


def test_confirm_reset_unknown_without_baseline():
    """No pre-/clear reading -> a collapse cannot be measured against anything."""
    state, why = w._confirm_reset("cc-orchestrator", None, wait_s=0, poll_s=0)
    assert state == w.CONFIRM_UNKNOWN
    assert "baseline" in why


def _happy_reset_seams(monkeypatch, paged, *, pane_raw="reading boot_briefing"):
    """Everything a full _do_reset needs, minus the telemetry — so each test below
    varies ONLY what the confirmation says."""
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True,
                                                input_text="", raw=pane_raw))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "│ > /clear │\n")
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))
    monkeypatch.setattr(w.time, "sleep", lambda s: None)


def test_do_reset_emits_NO_page_on_a_confirmed_boot(monkeypatch):
    """THE missing assertion. A reset that is PROVED and whose pane shows boot
    activity must page the operator ZERO times.

    Its absence is why the regression survived: the whole effect of the defect was
    "sends the operator a page that is not true", and no test looked at whether a
    page was sent at all."""
    paged = []
    _happy_reset_seams(monkeypatch, paged)
    _fingerprints(monkeypatch, FP_BEFORE, FP_AFTER_RESET)

    outcome = {}
    ok, detail = w._do_reset(_ctx(), REG, outcome)
    assert ok is True
    assert outcome["confirmation"] == w.CONFIRM_RESET
    assert paged == [], f"a confirmed, booting reset must page nobody; got {paged}"


def test_do_reset_confirmed_but_quiet_pane_hedges_the_RIGHT_half(monkeypatch):
    """When the reset is proved but the pane shows no boot activity, the page must
    ASSERT the proved reset and report the quiet pane — the exact inverse of the
    old copy, which asserted the UNVERIFIED reset ('cleared + boot nudge sent') and
    hedged the OBSERVED quiet ('no activity yet')."""
    paged = []
    _happy_reset_seams(monkeypatch, paged, pane_raw="")  # idle + nothing about booting
    _fingerprints(monkeypatch, FP_BEFORE, FP_AFTER_RESET)

    ok, _detail = w._do_reset(_ctx(), REG)
    assert ok is True
    assert len(paged) == 1
    assert "reset CONFIRMED" in paged[0]
    assert "NO activity" in paged[0]
    assert "cleared + boot nudge sent" not in paged[0]


def test_do_reset_pages_and_FAILS_when_reset_cannot_be_confirmed(monkeypatch):
    """Telemetry never refreshed: report it as NOT confirmed, tell the operator to
    treat the body as NOT reset, and return failure. Never 'cleared'."""
    paged = []
    _happy_reset_seams(monkeypatch, paged)
    _fingerprints(monkeypatch, FP_BEFORE, FP_STILL_SAME_PRE)
    monkeypatch.setattr(w, "_RESET_CONFIRM_WAIT_S", 0)

    outcome = {}
    ok, detail = w._do_reset(_ctx(), REG, outcome)
    assert ok is False
    assert outcome["confirmation"] == w.CONFIRM_UNKNOWN
    assert outcome["clear_submitted"] is True
    assert "UNCONFIRMED" in detail
    assert len(paged) == 1
    assert "could NOT confirm" in paged[0]
    assert "NOT reset" in paged[0]
    assert "cleared" not in paged[0]  # never claims the body was cleared


def test_do_reset_pages_and_FAILS_when_reset_is_refuted(monkeypatch):
    """Post-/clear telemetry still shows the old session: that is PROOF of failure
    and must read differently from 'could not confirm'."""
    paged = []
    _happy_reset_seams(monkeypatch, paged)
    _fingerprints(monkeypatch, FP_BEFORE, FP_STILL_SAME_PRE, FP_STILL_SAME_POST)
    monkeypatch.setattr(w, "_RESET_CONFIRM_WAIT_S", 60)
    monkeypatch.setattr(w, "_RESET_CONFIRM_POLL_S", 0)

    outcome = {}
    ok, detail = w._do_reset(_ctx(), REG, outcome)
    assert ok is False
    assert outcome["confirmation"] == w.CONFIRM_NOT_RESET
    assert "NOT RESET" in detail
    assert len(paged) == 1
    assert "was NOT reset" in paged[0] and "STILL FULL" in paged[0]


def test_boot_nudge_send_failure_does_not_claim_the_body_cleared(monkeypatch):
    """Same defect class: the old page said 'cleared OK but the BOOT nudge failed',
    asserting the half we had never verified."""
    paged = []
    _happy_reset_seams(monkeypatch, paged)
    _fingerprints(monkeypatch, FP_BEFORE)
    sent = {"n": 0}

    def _literal(reg, t):
        sent["n"] += 1
        return sent["n"] == 1  # the /clear types fine; the boot nudge fails
    monkeypatch.setattr(w, "_send_literal", _literal)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "boot nudge send failed" in detail
    assert paged and "UNVERIFIED" in paged[0]
    assert "cleared OK" not in paged[0]


def test_run_executor_reports_the_three_states_distinctly(monkeypatch, tmp_path):
    """A caller distinguishes the outcomes via the `outcome` out-param, and the
    printed line says which one it was — 'CONFIRMED' / 'refuted' / 'UNCONFIRMED'
    are never the same word."""
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_page_loud", lambda text: None)

    def _stub(state, ok):
        def _f(a, reg, outcome=None):
            if outcome is not None:
                outcome["clear_submitted"] = True
                outcome["confirmation"] = state
            return (ok, f"detail for {state}")
        return _f

    monkeypatch.setattr(w, "_do_reset", _stub(w.CONFIRM_NOT_RESET, False))
    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="red")
    assert "NOT-RESET (refuted by telemetry)" in res[0]
    # refuted -> NOT deduped: the next cycle should try again on a body we PROVED
    # is still full.
    assert "reset_at" not in w._load_exec_state().get("cc-orchestrator", {})

    monkeypatch.setattr(w, "_do_reset", _stub(w.CONFIRM_UNKNOWN, False))
    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="red")
    assert "UNCONFIRMED (not proven either way)" in res[0]
    # unconfirmed -> DEDUPED despite being a failure: re-driving a /clear+boot at a
    # body that may well have reset would wipe its fresh boot.
    assert w._load_exec_state()["cc-orchestrator"].get("reset_at")


# --------------------------------------------------------------------------- #
# PaneState field-binding guard (the contributing cause). Python 3.9 has no
# dataclasses.KW_ONLY, so mis-binding cannot be made impossible — it is made LOUD.
# --------------------------------------------------------------------------- #

def test_panestate_rejects_pane_text_bound_to_bg_agents():
    """The exact 2026-07-26 mis-binding: `bg_agents` was inserted ahead of `raw`,
    so PaneState(True, True, True, "", "<pane text>") silently made bg_agents a
    (truthy) string and left raw empty. It must now raise."""
    with pytest.raises(TypeError) as e:
        w.PaneState(True, True, True, "", "reading boot_briefing")
    assert "bg_agents" in str(e.value) and "KEYWORD" in str(e.value)


def test_panestate_rejects_bool_as_bg_agent_count():
    """bool is a subclass of int — True must not sail through as '1 agent'."""
    with pytest.raises(TypeError):
        w.PaneState(reachable=True, idle=True, authenticated=True, bg_agents=True)


def test_panestate_rejects_non_string_raw():
    with pytest.raises(TypeError):
        w.PaneState(reachable=True, idle=True, authenticated=True, raw=3)


def test_panestate_keyword_construction_is_unchanged():
    st = w.PaneState(reachable=True, idle=None, authenticated=None,
                     input_text="hi", bg_agents=4, raw="pane")
    assert (st.reachable, st.idle, st.authenticated) == (True, None, None)
    assert st.bg_agents == 4 and st.raw == "pane"


def test_pane_state_builds_a_valid_panestate_from_a_real_pane(monkeypatch):
    """_pane_state itself must satisfy the new type contract (it constructs with
    keywords; this pins that its bg_agents really is a count)."""
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40:
                        PANE_IDLE_AUTHED + "  Waiting for 3 background agents to finish (2m 10s)\n")
    st = w._pane_state(REG)
    assert st.bg_agents == 3
    assert st.raw.startswith("> resumed session")


# --------------------------------------------------------------------------- #
# Side-effect logs must not contaminate the real audit trail (2026-07-26: the
# suite wrote fabricated pen-gate trios and fabricated "preserved operator input"
# into logs/, interleaved with genuine captures and indistinguishable from them).
# --------------------------------------------------------------------------- #

def test_side_effect_logs_are_test_scoped(monkeypatch):
    assert w._logs_dir() != w._ORCH_DIR / "logs"
    w._log_pen_gate("synthetic fixture line — must never reach the real audit log")
    written = (w._logs_dir() / "pen_gate.log").read_text()
    assert "synthetic fixture line" in written
    real = w._ORCH_DIR / "logs" / "pen_gate.log"
    if real.exists():
        assert "synthetic fixture line" not in real.read_text()


def test_preserved_input_log_is_test_scoped(monkeypatch):
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)
    st = w.PaneState(reachable=True, idle=True, authenticated=True,
                     input_text="synthetic unsent text")
    w._preserve_input_box(REG, st)
    assert "synthetic unsent text" in (
        w._logs_dir() / "context_health_preserved_input.log").read_text()
    real = w._ORCH_DIR / "logs" / "context_health_preserved_input.log"
    if real.exists():
        assert "synthetic unsent text" not in real.read_text()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# --------------------------------------------------------------------------- #
# self-compacting bodies: the alert must not recommend what the operator overruled
# --------------------------------------------------------------------------- #
#
# The operator ruled on 2026-08-15 (op#13418): "im worried about the auto compaction
# because its not lossless." This alert was telling him the opposite — "no action
# needed, it auto-compacts" — about the bodies that most need recycling, and it fired
# on the console all night while it rode from 400k to 822k. Auto-compaction is the
# WORSE outcome, not the release valve: a body that hands off deliberately keeps what
# it chooses; a body that compacts keeps what a summarizer chose. Since 6eb9d01 there
# is a third option that did not exist when this copy was written — the body can
# recycle ITSELF on its own fresh handoff, with no operator button.

def _self_compacting_reg():
    return {"label": "cc-fleet-health", "self_compacts": True}


def test_self_compacting_alert_does_not_tell_the_operator_no_action_is_needed():
    text = w._alert_text(_ctx(agent="cc-fleet-health", pct=82, level="red"), _self_compacting_reg())
    assert "no action needed" not in text.lower(), (
        "This is the sentence the operator overruled: it reassures him about the body "
        "that most needs recycling, at exactly the point where it should hand off."
    )


def test_self_compacting_alert_names_deliberate_recycle_as_the_better_option():
    text = w._alert_text(_ctx(agent="cc-fleet-health", pct=82, level="red"), _self_compacting_reg())
    assert "self_recycle" in text or "recycle itself" in text.lower(), (
        "A self-compacting body at this level has a better option than compaction — "
        "recycling itself on its own fresh handoff (scripts/self_recycle.sh). The alert "
        "must point at it, not describe compaction as a solution."
    )


def test_self_compacting_alert_says_compaction_is_lossy():
    text = w._alert_text(_ctx(agent="cc-fleet-health", pct=82, level="red"), _self_compacting_reg())
    assert "lossy" in text.lower() or "not lossless" in text.lower(), (
        "The operator's objection is that compaction loses things. The alert must say so "
        "rather than presenting compaction as a clean release valve."
    )


# --------------------------------------------------------------------------- #
# trigger inversion: tell the BODY, not only the operator
# --------------------------------------------------------------------------- #
#
# The operator, op#13520: "thats what ive been saying 1000 times and yet here i am
# telling you youre bloated and having to push a button." The detector has always
# reported bloat to a HUMAN. Now that a body can recycle itself (6eb9d01) the report
# should go to the BODY — it is the only party that knows whether it is mid-thought,
# and it carries the whole cost of being wrong, which is exactly why this needs no
# arm-sign while a third party clearing it would.

class _FakeCur:
    def __init__(self, log): self.log = log
    def execute(self, sql, params=None): self.log.append((sql, params))
    def fetchone(self): return (1,)
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, log): self.log = log
    def cursor(self): return _FakeCur(self.log)
    def commit(self): self.log.append(("COMMIT", None))
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_bloat_nudge_is_addressed_to_the_body_not_the_operator():
    log = []
    ok = w._bus_nudge_self_recycle("cc-fleet-health", 82, connect=lambda dsn: _FakeConn(log),
                                   dsn="postgres://fake")
    assert ok is True
    sql, params = log[0]
    assert "agent_messages" in sql and "insert" in sql.lower()
    assert "cc-fleet-health" in params, "the nudge must be addressed to the bloated body itself"


def test_bloat_nudge_tells_the_body_the_decision_is_its_own():
    log = []
    w._bus_nudge_self_recycle("cc-fleet-health", 82, connect=lambda dsn: _FakeConn(log),
                              dsn="postgres://fake")
    body = " ".join(str(p) for p in log[0][1])
    assert "self_recycle" in body, "it must name the tool, not just the condition"
    assert "handoff" in body.lower(), "recycling onto a stale restore point launders the loss"
    assert "your call" in body.lower() or "you decide" in body.lower(), (
        "a body clearing itself is not being ordered to — it is the only party that knows "
        "whether it is finished, and the alert must say so or it becomes an inference again"
    )


def test_bloat_nudge_reports_failure_rather_than_claiming_a_send_it_did_not_make():
    def _boom(dsn): raise RuntimeError("db down")
    assert w._bus_nudge_self_recycle("cc-fleet-health", 82, connect=_boom, dsn="x") is False


def test_alert_does_not_claim_a_nudge_that_did_not_happen():
    """no-fake-autopilot: the alert used to say 'Nudging it' unconditionally. An alert
    that reports an action it did not take is worse than one that stays quiet — the
    operator stands down believing the body was told."""
    reg = _self_compacting_reg()
    a = _ctx(agent="cc-fleet-health", pct=82, level="red")
    failed = w._alert_text(a, reg, nudged=False)
    assert "nudging it" not in failed.lower()
    assert "could not" in failed.lower() or "couldn't" in failed.lower()
    done = w._alert_text(a, reg, nudged=True)
    assert "nudged it" in done.lower()


# ── #40 / autoscaler S1: activity-based alerting + the ended-session guard ─────────

def test_run_alerts_skips_a_stale_reading(monkeypatch, tmp_path):
    """SAFETY (the whole risk): a STALE reading (body stopped writing telemetry -> offline/
    dead) must NOT page — it is LAST-KNOWN, not current (a downed cc-quality reads 95% for
    hours; ended_at is a live-updated activity stamp so a LIVE body stays fresh). A wrong
    'page' here is a Telegram page claiming an offline body is bloated. Consistent with the
    reset path, which already refuses a stale body."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "alert_state.json")
    sent = []
    monkeypatch.setattr(w, "_send_alert", lambda text: sent.append(text))
    a = w.AgentCtx(agent="cc-quality", ctx_tokens=950_000, pct=95, level="red",
                   age_s=99_999, action="reset-eligible", stale=True)
    fired = w.run_alerts([a])
    assert fired == [], "a stale (last-known) reading must not page"
    assert sent == [], "no operator page on stale telemetry"


def test_run_alerts_self_compacts_body_self_nudges_no_operator_page(monkeypatch, tmp_path):
    """S2 (supersedes the S1 page-on-rise behaviour): cc-quality is now a self_compacts
    body, so a FRESH red reading SELF-NUDGES it on the bus and does NOT page the operator
    — the decouple. The operator page is now a BACKSTOP (see backstop tests), not the
    routine response to a self-recyclable body being bloated."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "alert_state.json")
    pages = []
    nudges = []
    monkeypatch.setattr(w, "_send_alert", lambda text: pages.append(text))
    monkeypatch.setattr(w, "_bus_nudge_self_recycle",
                        lambda agent, pct, *a, **k: (nudges.append((agent, pct)) or True))
    a = w.AgentCtx(agent="cc-quality", ctx_tokens=780_000, pct=78, level="amber",
                   age_s=30, action="reset-eligible", stale=False)
    fired = w.run_alerts([a])
    assert nudges == [("cc-quality", 78)], "a self_compacts body must be self-nudged"
    assert pages == [], "requirement (a): ZERO operator page at the nudge threshold"
    assert fired == ["cc-quality"]


def test_registry_s2_flags():
    """S2: the three self-compacting Claude Code bodies now carry self_compacts:True so the
    self-recycle nudge (gated on that flag at run_alerts) reaches them, not only orch-console.
    - cc-quality: self_compacts added, auto_reset stays False (on-demand, never /clear'd).
    - cai: self_compacts added PURELY ADDITIVELY — auto_reset stays True, the reset path is
      untouched (Nazim point 2: nudge-path and reset-path strictly separated).
    - cc-fleet-health (the SRE, self): now registered with self_compacts:True + auto_reset:False
      + alerts:False -> it self-nudges with NO operator page, and is NEVER executor-/clear'd."""
    q = w._AGENT_REGISTRY.get("cc-quality")
    assert q and q.get("self_compacts") is True and q.get("auto_reset") is False

    cai = w._AGENT_REGISTRY.get("cai")
    assert cai and cai.get("self_compacts") is True
    assert cai.get("auto_reset") is True, "cai reset path must be UNCHANGED (purely additive)"

    sre = w._AGENT_REGISTRY.get("cc-fleet-health")
    assert sre is not None, "the SRE must be registered in S2 (self-nudge path)"
    assert sre.get("self_compacts") is True
    assert sre.get("auto_reset") is False, "the SRE is NEVER auto-reset (CAI-501 dead-man = lease)"
    assert not sre.get("alerts"), "the SRE must not take a plain operator page (self-nudge only)"


# ── S2: self-recycle NUDGE decouple + operator BACKSTOP ───────────────────────────

def _sc_seams(monkeypatch, tmp_path, nudge_ok=True):
    """Patch the two side-effect seams and the state file; return (nudges, pages) recorders."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "alert_state.json")
    nudges, pages = [], []
    monkeypatch.setattr(w, "_bus_nudge_self_recycle",
                        lambda agent, pct, *a, **k: (nudges.append((agent, pct)) or nudge_ok))
    monkeypatch.setattr(w, "_send_alert", lambda text: pages.append(text))
    return nudges, pages


def _sc_reg(monkeypatch, agent="sc-body", **extra):
    reg = {"label": agent, "window": 1_000_000, "self_compacts": True,
           "alerts": False, "auto_reset": False, **extra}
    monkeypatch.setitem(w._AGENT_REGISTRY, agent, reg)
    return reg


def test_s2_below_nudge_threshold_does_not_nudge(monkeypatch, tmp_path):
    """nudge@70% (Nazim #25288): a self_compacts body under 70% is NOT nudged (amber alone
    is not the nudge line)."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    fired = w.run_alerts([_ctx(agent="sc-body", pct=65, level="amber")])
    assert nudges == [] and pages == [] and fired == []


def test_s2_at_threshold_nudges_no_page(monkeypatch, tmp_path):
    """requirement (a): at/above 70% the body self-nudges, no operator page."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    fired = w.run_alerts([_ctx(agent="sc-body", pct=71, level="amber")])
    assert nudges == [("sc-body", 71)]
    assert pages == []


def test_s2_dedup_no_storm_without_a_rise(monkeypatch, tmp_path):
    """dedup on level-RISE +10% (nudge-storm lesson 2026-07-08): the same body sitting at
    the same pct across cycles is nudged ONCE, not every poll."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    w.run_alerts([_ctx(agent="sc-body", pct=72, level="amber")])
    w.run_alerts([_ctx(agent="sc-body", pct=74, level="amber")])  # +2% < 10 -> no re-nudge
    assert nudges == [("sc-body", 72)], "must not re-nudge without a >=10% rise"
    assert pages == []


def test_s2_renudges_on_a_ten_point_rise(monkeypatch, tmp_path):
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    w.run_alerts([_ctx(agent="sc-body", pct=72, level="amber")])
    w.run_alerts([_ctx(agent="sc-body", pct=83, level="red")])  # +11% -> re-nudge
    assert nudges == [("sc-body", 72), ("sc-body", 83)]
    assert pages == []


def test_s2_backstop_pages_after_n_nudges_without_recycle(monkeypatch, tmp_path):
    """requirement (b): a body that IGNORES the nudge (keeps rising, never recycles) past the
    persisted N-nudge threshold gets an operator BACKSTOP page — self-recycle has demonstrably
    failed and silence would mask a stuck body."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_N", 3)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_PCT", 99)  # isolate the count trigger
    w.run_alerts([_ctx(agent="sc-body", pct=71, level="amber")])   # nudge 1
    w.run_alerts([_ctx(agent="sc-body", pct=82, level="red")])     # nudge 2
    assert pages == [], "no backstop before the threshold"
    w.run_alerts([_ctx(agent="sc-body", pct=93, level="red")])     # nudge 3 -> backstop
    assert len(pages) == 1, "backstop must page after N nudges without recycle"
    assert "sc-body" in pages[0]


def test_s2_backstop_pages_once_per_episode(monkeypatch, tmp_path):
    """The backstop is a latch: it pages ONCE per episode, not every cycle (no operator spam)."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_PCT", 90)
    w.run_alerts([_ctx(agent="sc-body", pct=92, level="red")])   # first contact -> nudge only
    w.run_alerts([_ctx(agent="sc-body", pct=94, level="red")])   # prior cycle seen -> backstop
    w.run_alerts([_ctx(agent="sc-body", pct=95, level="red")])   # already paged -> silent
    assert len(pages) == 1, "backstop must page once per episode, not re-page every cycle"


def test_s2_first_contact_at_danger_pct_reachable_does_not_backstop(monkeypatch, tmp_path):
    """The danger-pct backstop must NOT fire on FIRST contact with a reachable high body — it was
    just nudged, it has not ignored anything (cai legitimately rides near 90%; a first-cycle
    'self-recycle NOT happening, drive a reset' page would be a false alarm about a healthy body)."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_PCT", 90)
    w.run_alerts([_ctx(agent="sc-body", pct=93, level="red")])
    assert nudges == [("sc-body", 93)], "it IS nudged on first contact"
    assert pages == [], "but NOT backstop-paged on first contact when reachable"


def test_s2_danger_pct_backstop_fires_on_the_next_cycle(monkeypatch, tmp_path):
    """A reachable body that stays at the danger line ACROSS cycles (had the nudge, did not
    recycle) does get the backstop on the subsequent cycle — the 'ignored past the line' case."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_PCT", 90)
    w.run_alerts([_ctx(agent="sc-body", pct=93, level="red")])   # first contact -> nudge, no page
    w.run_alerts([_ctx(agent="sc-body", pct=93, level="red")])   # still high a cycle later -> backstop
    assert len(pages) == 1


def test_s2_old_schema_state_does_not_immediately_backstop(monkeypatch, tmp_path):
    """REGRESSION (cai's exact live case): a pre-existing S1-schema state entry ({level,alerted_at},
    no nudge fields) must be treated as FIRST contact by the nudge path — so a body already at the
    danger line is NUDGED, not immediately backstop-paged, on the first new-code cycle."""
    state_file = tmp_path / "alert_state.json"
    import json as _json
    state_file.write_text(_json.dumps({"sc-body": {"level": "red", "alerted_at": 1_700_000_000.0}}))
    monkeypatch.setattr(w, "_STATE_FILE", state_file)
    nudges, pages = [], []
    monkeypatch.setattr(w, "_bus_nudge_self_recycle",
                        lambda agent, pct, *a, **k: (nudges.append((agent, pct)) or True))
    monkeypatch.setattr(w, "_send_alert", lambda text: pages.append(text))
    _sc_reg(monkeypatch)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_PCT", 90)
    w.run_alerts([_ctx(agent="sc-body", pct=92, level="red")])
    assert nudges == [("sc-body", 92)], "old-schema entry -> nudged as first contact"
    assert pages == [], "old-schema entry must NOT trigger an immediate false backstop page"


def test_s2_backstop_pages_when_climbs_dangerously_high(monkeypatch, tmp_path):
    """The second backstop trigger: a body that has climbed past the danger line pages even if
    the nudge could not be delivered (nudge_ok=False -> count never advances) — an unreachable,
    dangerously-high body must still surface."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path, nudge_ok=False)
    _sc_reg(monkeypatch)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_PCT", 90)
    w.run_alerts([_ctx(agent="sc-body", pct=93, level="red")])
    assert nudges == [("sc-body", 93)], "it still tries to nudge"
    assert len(pages) == 1, "a dangerously-high body backstop-pages even when the nudge failed"


def test_s2_failed_nudge_is_not_counted(monkeypatch, tmp_path):
    """no-fake-autopilot: a nudge that did NOT send must not advance the nudge count toward the
    backstop (else a persistently-unreachable body would look 'nudged N times')."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path, nudge_ok=False)
    _sc_reg(monkeypatch)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_N", 2)
    monkeypatch.setattr(w, "_NUDGE_BACKSTOP_PCT", 99)  # isolate the count path
    w.run_alerts([_ctx(agent="sc-body", pct=71, level="amber")])
    w.run_alerts([_ctx(agent="sc-body", pct=82, level="red")])
    assert pages == [], "failed nudges must not accumulate toward the backstop count"


def test_s2_recycle_resets_the_episode(monkeypatch, tmp_path):
    """When the body recycles (a sharp ctx drop), the episode state clears so a later rise
    starts a fresh nudge cycle — the whole point of the loop working."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    w.run_alerts([_ctx(agent="sc-body", pct=82, level="red")])          # nudge
    w.run_alerts([_ctx(agent="sc-body", pct=12, level="green")])        # recycled -> clear
    w.run_alerts([_ctx(agent="sc-body", pct=80, level="red")])          # fresh episode -> nudge again
    assert nudges == [("sc-body", 82), ("sc-body", 80)]
    assert pages == []


def test_s2_stale_reading_still_skipped(monkeypatch, tmp_path):
    """The S1 stale-guard must survive S2: a stale (last-known) reading neither nudges nor
    pages nor backstops — a downed body reads 95% for hours."""
    nudges, pages = _sc_seams(monkeypatch, tmp_path)
    _sc_reg(monkeypatch)
    a = w.AgentCtx(agent="sc-body", ctx_tokens=950_000, pct=95, level="red",
                   age_s=99_999, action="reset-eligible", stale=True)
    fired = w.run_alerts([a])
    assert nudges == [] and pages == [] and fired == []


def test_s2_cai_reset_path_untouched_by_self_compacts(monkeypatch, tmp_path):
    """Nazim point 2 (verify, don't assume): adding self_compacts:True to cai must touch NO
    branch keyed off auto_reset. run_executor selects bodies by auto_reset and never reads
    self_compacts, so cai (auto_reset:True, now also self_compacts:True) under --arm=amber
    still does the write-only checkpoint half and NEVER _do_reset — the reset path is
    unchanged by S2."""
    calls = {"checkpoint": 0, "reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(reachable=True, idle=True, authenticated=True, input_text="", raw=""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)  # force a checkpoint
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "fresh handoff x.md"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg, outcome=None: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK"))[1])
    out = w.run_executor([_ctx(agent="cai", pct=89, level="red", action="reset-eligible")], "amber")
    assert calls["checkpoint"] == 1, "cai still write-only checkpoints under amber"
    assert calls["reset"] == 0, "cai is NEVER reset under --arm=amber — self_compacts changed nothing here"
    assert any("cai" in line for line in out)


# --------------------------------------------------------------------------- #
# Transient-substrate-blip hardening (2026-08-31): a DNS/connect blip to the
# pooler must NOT false-page "watchdog CRASHED"; a single skip self-heals next
# tick (soft, no page), a PERSISTENT streak still pages LOUD (dead-man's-switch).
# _page_loud is autouse-RAISE, so a test that reaches its assert without patching
# it has proven no loud page fired.
# --------------------------------------------------------------------------- #

def _ge_args(alert=True, json_=False):
    return types.SimpleNamespace(alert=alert, json=json_, arm=None)


def test_gauge_unreachable_single_skip_is_soft_no_page(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    rc = w._handle_gauge_unreachable(w.GaugeUnreachable("failed to resolve host"), _ge_args())
    assert rc == 0  # clean exit — no external 'crashed' signal
    assert json.loads((tmp_path / "exec.json").read_text())["gauge_unreachable_streak"] == 1
    # reaching here without _page_loud (autouse-raise) firing == no page on one blip


def test_gauge_unreachable_persistent_pages_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    (tmp_path / "exec.json").write_text(json.dumps({"gauge_unreachable_streak": 1}))
    pages: list[str] = []
    monkeypatch.setattr(w, "_page_loud", lambda text: pages.append(text))
    rc = w._handle_gauge_unreachable(w.GaugeUnreachable("dns dead"), _ge_args())
    assert rc == 0
    assert json.loads((tmp_path / "exec.json").read_text())["gauge_unreachable_streak"] == 2
    assert len(pages) == 1 and "BLIND" in pages[0] and "NOT guarding" in pages[0]


def test_gauge_unreachable_dryrun_never_pages(monkeypatch, tmp_path):
    # Even deep into a persistent streak, a NON-alert (dry-run) invocation must not
    # page. _page_loud stays autouse-raise; reaching the assert proves it was skipped.
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    (tmp_path / "exec.json").write_text(json.dumps({"gauge_unreachable_streak": 5}))
    rc = w._handle_gauge_unreachable(w.GaugeUnreachable("x"), _ge_args(alert=False))
    assert rc == 0
    assert json.loads((tmp_path / "exec.json").read_text())["gauge_unreachable_streak"] == 6


def test_gauge_unreachable_streak_clears_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    (tmp_path / "exec.json").write_text(json.dumps({"gauge_unreachable_streak": 3, "other": "keep"}))
    w._clear_gauge_unreachable_streak()
    st = json.loads((tmp_path / "exec.json").read_text())
    assert st["gauge_unreachable_streak"] == 0
    assert st["other"] == "keep"  # unrelated state preserved


def test_read_context_gauge_retries_then_raises_gauge_unreachable(monkeypatch):
    # Force the connect to always fail with OperationalError; assert it retries
    # _GAUGE_CONNECT_ATTEMPTS times and converts to GaugeUnreachable (never a raw
    # OperationalError that would hit the __main__ 'CRASHED' page).
    import psycopg  # same module read_context_gauge imports
    calls = {"n": 0}

    def _boom(dsn):
        calls["n"] += 1
        raise psycopg.OperationalError("failed to resolve host 'x.pooler'")

    monkeypatch.setattr(w, "_dsn", lambda: "postgresql://u:p@x.pooler:5432/db")
    monkeypatch.setattr(psycopg, "connect", _boom)
    monkeypatch.setattr(w.time, "sleep", lambda *_a, **_k: None)  # no real backoff wait
    with pytest.raises(w.GaugeUnreachable):
        w.read_context_gauge([])
    assert calls["n"] == w._GAUGE_CONNECT_ATTEMPTS


# Recovery all-clear (2026-08-31, Nazim review follow-up): a LOUD "BLIND — NOT
# guarding" page must be bookended by a RECOVERY all-clear so the operator gets
# closure; a soft single-skip that recovered never paged, so it stays silent.

def test_gauge_recovery_after_loud_page_sends_all_clear(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    (tmp_path / "exec.json").write_text(
        json.dumps({"gauge_unreachable_streak": 3, "gauge_unreachable_paged": True, "keep": 1}))
    pages: list[str] = []
    monkeypatch.setattr(w, "_page_loud", lambda text: pages.append(text))
    w._clear_gauge_unreachable_streak(alert=True)
    st = json.loads((tmp_path / "exec.json").read_text())
    assert st["gauge_unreachable_streak"] == 0
    assert "gauge_unreachable_paged" not in st
    assert st["keep"] == 1  # unrelated state preserved
    assert len(pages) == 1 and "RECOVERED" in pages[0] and "3 run" in pages[0]


def test_gauge_recovery_after_soft_skip_is_silent(monkeypatch, tmp_path):
    # streak=1 never loud-paged; recovery must NOT page. _page_loud stays
    # autouse-raise, so reaching the assert proves no all-clear fired.
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    (tmp_path / "exec.json").write_text(json.dumps({"gauge_unreachable_streak": 1}))
    w._clear_gauge_unreachable_streak(alert=True)
    assert json.loads((tmp_path / "exec.json").read_text())["gauge_unreachable_streak"] == 0


def test_gauge_recovery_dryrun_never_all_clears(monkeypatch, tmp_path):
    # Even a genuinely loud-paged streak recovers silently when not in --alert mode.
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    (tmp_path / "exec.json").write_text(
        json.dumps({"gauge_unreachable_streak": 2, "gauge_unreachable_paged": True}))
    w._clear_gauge_unreachable_streak(alert=False)
    st = json.loads((tmp_path / "exec.json").read_text())
    assert st["gauge_unreachable_streak"] == 0 and "gauge_unreachable_paged" not in st


def test_gauge_loud_then_recover_bookends_exactly_two_pages(monkeypatch, tmp_path):
    # Full lifecycle: soft skip -> loud page -> recovery all-clear == exactly 2 pages.
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    pages: list[str] = []
    monkeypatch.setattr(w, "_page_loud", lambda text: pages.append(text))
    args = _ge_args(alert=True)
    w._handle_gauge_unreachable(w.GaugeUnreachable("blip"), args)   # streak 1 - soft, no page
    assert pages == []
    w._handle_gauge_unreachable(w.GaugeUnreachable("blip"), args)   # streak 2 - LOUD page
    assert len(pages) == 1 and "BLIND" in pages[0]
    w._clear_gauge_unreachable_streak(alert=True)                    # recovery - ALL-CLEAR
    assert len(pages) == 2 and "RECOVERED" in pages[1]
    st = json.loads((tmp_path / "exec.json").read_text())
    assert st["gauge_unreachable_streak"] == 0 and "gauge_unreachable_paged" not in st


# VALUE-FREEZE guard (2026-09-01): a broken cost-writer can refresh ended_at (row looks
# fresh, a.stale=False) while latest_context_tokens is FROZEN — a zombie reading that was
# re-paging the operator hourly. run_alerts must refuse the %-page on an unchanged value and
# surface the frozen gauge ONCE. cc-orchestrator is the only alerts-plain registry body.

def _amber_orch(pct=61):
    return _ctx(agent="cc-orchestrator", pct=pct, level="amber", action="checkpoint-nudge")


def test_alerts_hub_amber_steady_state_does_not_page_but_seeds_freeze(monkeypatch, tmp_path):
    """CAI-RESP-1360: the hub is a long-lived, EXTERNALLY-recycled singleton — amber/steady-state
    is EXPECTED, not degradation, so a fresh amber reading must NOT page the operator (this
    supersedes the S1 page-on-amber-rise behaviour). The value-freeze bookkeeping still seeds."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    row = _amber_orch()
    fired = w.run_alerts([row])
    assert sent == [], "hub amber steady-state must NOT operator-page (externally-recycled profile)"
    assert fired == []
    # value-freeze bookkeeping still runs (seeds the token fingerprint) even though no page fired
    st = __import__("json").loads((tmp_path / "state.json").read_text())
    assert st["__ctx_freeze__"]["cc-orchestrator"]["tokens"] == row.ctx_tokens


def test_alerts_value_frozen_suppresses_pct_and_pages_frozen_once(monkeypatch, tmp_path):
    import json, time
    row = _amber_orch()
    seed = {"__ctx_freeze__": {"cc-orchestrator": {"tokens": row.ctx_tokens,
            "since": time.time() - (w._CTX_FROZEN_S + 600), "paged": False}}}
    (tmp_path / "state.json").write_text(json.dumps(seed))
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    # cc-orchestrator (the hub) is cross-host — its pane is unreachable from the Mini, so the
    # idle-vs-active gate falls back to the value band; at amber (61%) an amber+ frozen value
    # is a genuine hidden-bloat risk and MUST still page (Nazim 36318/36319, op#18601).
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: None)
    w.run_alerts([row])
    # exactly one alert, and it's the FROZEN one — NOT the climbing-% amber page
    assert len(sent) == 1 and "FROZEN" in sent[0] and "ZOMBIE" in sent[0]
    # CAI-1360: the hub is externally-recycled, so the frozen page names the REAL remediation
    # (the parameterized external-recycle string, pinned by orch-console), never self-recycle.
    assert w._HUB_RECYCLE_REMEDIATION in sent[0]
    assert "self_recycle" not in sent[0] and "recycle itself" not in sent[0].lower()
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["__ctx_freeze__"]["cc-orchestrator"]["paged"] is True
    assert "cc-orchestrator" not in {k for k in st if k != "__ctx_freeze__"}  # no level-alert state


def test_alerts_frozen_already_paged_no_duplicate(monkeypatch, tmp_path):
    import json, time
    row = _amber_orch()
    seed = {"__ctx_freeze__": {"cc-orchestrator": {"tokens": row.ctx_tokens,
            "since": time.time() - (w._CTX_FROZEN_S + 600), "paged": True}}}
    (tmp_path / "state.json").write_text(json.dumps(seed))
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    w.run_alerts([row])
    assert sent == []  # already paged the freeze; no re-spam


def test_alerts_frozen_value_moves_bookends_all_clear(monkeypatch, tmp_path):
    import json, time
    row = _amber_orch(pct=61)  # ctx_tokens=610000
    seed = {"__ctx_freeze__": {"cc-orchestrator": {"tokens": 999999,  # DIFFERENT old value
            "since": time.time() - (w._CTX_FROZEN_S + 600), "paged": True}}}
    (tmp_path / "state.json").write_text(json.dumps(seed))
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    w.run_alerts([row])
    # value moved after a frozen-page -> an all-clear goes out
    assert any("LIVE again" in t for t in sent)
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["__ctx_freeze__"]["cc-orchestrator"]["paged"] is False


# ── CAI-RESP-1360: the hub's EXTERNAL-RECYCLE profile ─────────────────────────────
# The hub (cc-orchestrator) is long-lived, harness-compacted, and recycled ONLY via
# scripts/reset_orch.sh — it does NOT self-recycle. Amber/steady-state is EXPECTED (no page);
# only the ceiling (>=95%) or a frozen/non-advancing gauge pages, and the remediation names
# the external recycle, never self-recycle language.

def test_registry_hub_external_recycle_profile():
    """The hub carries external_recycle:True, keeps alerts:True (the frozen-page path is gated
    on reg.alerts) and auto_reset:True (executor path UNCHANGED — this is a paging-only change),
    and is NOT self_compacts (it cannot self-recycle — that was cai's reason to reject Option A)."""
    hub = w._AGENT_REGISTRY.get("cc-orchestrator")
    assert hub is not None
    assert hub.get("external_recycle") is True
    assert hub.get("alerts") is True, "frozen-page path is gated on reg.alerts"
    assert hub.get("auto_reset") is True, "executor/auto_reset path must be untouched"
    assert not hub.get("self_compacts"), "the hub does NOT self-recycle (CAI-1360 rejected Option A)"


def _er_reg(monkeypatch, agent="er-body", **extra):
    reg = {"label": agent, "window": 1_000_000, "alerts": True, "external_recycle": True,
           "auto_reset": True, "host": "test-host", "tmux": agent, **extra}
    monkeypatch.setitem(w._AGENT_REGISTRY, agent, reg)
    return reg


def test_er_amber_steady_state_does_not_page(monkeypatch, tmp_path):
    """Amber (below the ceiling) is EXPECTED for an externally-recycled body -> NO operator page,
    across the whole amber band (not just the low edge)."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    _er_reg(monkeypatch)
    for pct in (62, 78):  # low and high amber
        sent.clear()
        fired = w.run_alerts([_ctx(agent="er-body", pct=pct, level="amber")])
        assert sent == [], f"amber {pct}% must not page an externally-recycled body"
        assert fired == []


def test_er_at_ceiling_pages_external_recycle_not_self_recycle(monkeypatch, tmp_path):
    """At/above the page line (>=95%) the operator IS paged, with reset_orch.sh remediation and
    NO self-recycle language (the hub cannot self-recycle)."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    _er_reg(monkeypatch)
    fired = w.run_alerts([_ctx(agent="er-body", pct=96, level="red")])
    assert len(sent) == 1, "a body at the ceiling must page the operator"
    assert w._HUB_RECYCLE_REMEDIATION in sent[0], "remediation must name the parameterized external recycle"
    assert "self_recycle" not in sent[0] and "recycle itself" not in sent[0].lower(), \
        "must NEVER use self-recycle language for an externally-recycled body"
    assert fired == ["er-body"]


def test_er_ceiling_page_latches_then_renags(monkeypatch, tmp_path):
    """The ceiling page latches once per episode (no per-cycle spam) but red-style re-nags after
    _RENAG_MIN so a body pinned at the wall keeps surfacing until recovered."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    _er_reg(monkeypatch)
    w.run_alerts([_ctx(agent="er-body", pct=96, level="red")])   # page 1
    w.run_alerts([_ctx(agent="er-body", pct=97, level="red")])   # same episode, <renag -> silent
    assert len(sent) == 1, "must not re-page every cycle at the ceiling"
    # fast-forward past the re-nag window by ageing the stored alerted_at
    import json
    st = json.loads((tmp_path / "state.json").read_text())
    st["er-body"]["alerted_at"] -= (w._RENAG_MIN * 60 + 1)
    (tmp_path / "state.json").write_text(json.dumps(st))
    w.run_alerts([_ctx(agent="er-body", pct=97, level="red")])   # past renag -> pages again
    assert len(sent) == 2, "must re-nag after _RENAG_MIN while still pinned at the ceiling"


def test_er_drop_below_ceiling_clears_episode(monkeypatch, tmp_path):
    """Falling back below the page line ends the episode (state cleared), so a later re-climb
    to the ceiling pages fresh rather than being suppressed as already-paged."""
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    _er_reg(monkeypatch)
    w.run_alerts([_ctx(agent="er-body", pct=96, level="red")])   # page 1
    w.run_alerts([_ctx(agent="er-body", pct=70, level="amber")]) # dropped below ceiling -> clear
    w.run_alerts([_ctx(agent="er-body", pct=96, level="red")])   # fresh episode -> page again
    assert len(sent) == 2, "a re-climb to the ceiling after dropping must page fresh"


# ── IDLE-vs-ACTIVE gate on the frozen-gauge page (Nazim 36318 -> 36319, op#18601) ──
# The value-freeze guard correctly caught cc-quality's static gauge, but cc-quality is an
# on-demand auditor sitting IDLE at 13% (GREEN) — no hidden bloat — and it false-paged the
# operator. Fix: only page a frozen gauge when the body is ACTIVE (pane busy), or when the
# frozen value is high-band (amber+, where hidden bloat genuinely matters). Suppress the
# operator page for an idle/unreachable GREEN-band frozen lane. The discriminator is
# idle-vs-active (36319), NOT the band alone: a writer breaking low-% while the body keeps
# bloating is a green-frozen TRUE positive (busy pane) that must still page.

def _frozen_green_reg(monkeypatch, agent="odemand", **extra):
    """An on-demand alerts body (cc-quality shape): alerts:True so the frozen-page path is
    reachable, self_compacts:True, never auto-reset."""
    reg = {"label": agent, "window": 1_000_000, "alerts": True, "self_compacts": True,
           "auto_reset": False, "host": "self", "tmux": agent, **extra}
    monkeypatch.setitem(w._AGENT_REGISTRY, agent, reg)
    return reg


def _seed_frozen(tmp_path, monkeypatch, row, paged=False):
    import json, time
    seed = {"__ctx_freeze__": {row.agent: {"tokens": row.ctx_tokens,
            "since": time.time() - (w._CTX_FROZEN_S + 600), "paged": paged}}}
    (tmp_path / "state.json").write_text(json.dumps(seed))
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    return sent


def test_frozen_green_idle_lane_suppresses_operator_page(monkeypatch, tmp_path):
    """op#18601: cc-quality idle at 13% (GREEN) with a static gauge must NOT page the operator.
    An idle lane's gauge is legitimately frozen — there is no hidden bloat at 13%."""
    _frozen_green_reg(monkeypatch)
    row = w.AgentCtx(agent="odemand", ctx_tokens=130_000, pct=13, level="green",
                     age_s=30, action="ok", stale=False)
    sent = _seed_frozen(tmp_path, monkeypatch, row)
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: True)  # empty composer, no active turn
    fired = w.run_alerts([row])
    assert sent == [], "an IDLE green-band frozen lane must not page the operator (op#18601)"
    assert fired == []
    # paged stays False -> if it later turns active, the next cycle can still page (delayed, not lost)
    import json
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["__ctx_freeze__"]["odemand"]["paged"] is False


def test_frozen_green_BUSY_lane_pages_the_true_positive(monkeypatch, tmp_path):
    """Nazim 36319 correctness nuance: a writer breaking at LOW % while the body keeps WORKING
    and bloating is a GREEN-frozen TRUE positive. Band-alone would suppress it; the idle-vs-
    active gate pages it because the pane is BUSY (actively producing)."""
    _frozen_green_reg(monkeypatch)
    row = w.AgentCtx(agent="odemand", ctx_tokens=130_000, pct=13, level="green",
                     age_s=30, action="ok", stale=False)
    sent = _seed_frozen(tmp_path, monkeypatch, row)
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: False)  # mid-turn: "esc to interrupt"
    fired = w.run_alerts([row])
    assert len(sent) == 1 and "FROZEN" in sent[0], "an ACTIVE frozen lane must page regardless of band"
    assert fired == ["odemand"]
    import json
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["__ctx_freeze__"]["odemand"]["paged"] is True


def test_frozen_green_unreachable_lane_suppresses_operator_page(monkeypatch, tmp_path):
    """Pane unreachable (idle unknown) + GREEN band: either an idle lane or a low-context
    broken writer — nothing high hidden, so suppress the operator page."""
    _frozen_green_reg(monkeypatch)
    row = w.AgentCtx(agent="odemand", ctx_tokens=130_000, pct=13, level="green",
                     age_s=30, action="ok", stale=False)
    sent = _seed_frozen(tmp_path, monkeypatch, row)
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: None)  # pane unreachable
    fired = w.run_alerts([row])
    assert sent == [] and fired == []


def test_frozen_amber_unreachable_still_pages(monkeypatch, tmp_path):
    """The case that MATTERS (the hub-@61% cross-host zombie): pane UNREACHABLE (idle is None) +
    amber+ frozen -> PAGE via the band arm. The Mini can't see the cross-host hub's pane, and a
    genuinely-stuck 53h writer must still be surfaced."""
    _frozen_green_reg(monkeypatch, agent="hubish")
    row = w.AgentCtx(agent="hubish", ctx_tokens=610_000, pct=61, level="amber",
                     age_s=30, action="checkpoint-nudge", stale=False)
    sent = _seed_frozen(tmp_path, monkeypatch, row)
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: None)  # cross-host: pane unreachable
    fired = w.run_alerts([row])
    assert len(sent) == 1 and "FROZEN" in sent[0], "amber+ frozen + unreachable must page (hub zombie)"
    assert fired == ["hubish"]


def test_frozen_amber_confirmed_idle_suppresses(monkeypatch, tmp_path):
    """op#18837 ROOT fix (Nazim 36707): a CONFIRMED-idle body (pane not busy) frozen at AMBER must
    NOT page — for a local body 'frozen' implies idle, and an idle body hides no bloat at any band.
    This is orch-console frozen-amber overnight; the earlier 'amber+ always pages' rule false-paged it."""
    _frozen_green_reg(monkeypatch, agent="consoleish")
    row = w.AgentCtx(agent="consoleish", ctx_tokens=700_000, pct=70, level="amber",
                     age_s=30, action="checkpoint-nudge", stale=False)
    sent = _seed_frozen(tmp_path, monkeypatch, row)
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: True)  # CONFIRMED idle (pane not busy)
    fired = w.run_alerts([row])
    assert sent == [], "a confirmed-idle amber-frozen body must NOT page (op#18837 root fix)"
    assert fired == []


def test_frozen_repage_within_cooldown_suppressed_no_flap(monkeypatch, tmp_path):
    """op#18830: a BURSTY writer (cai) freezes -> moves a little -> re-freezes, repeatedly. Once
    the operator has been told the gauge is frozen, a NEW frozen episode WITHIN the re-page
    cooldown must NOT re-page — that repetition is the FROZEN/LIVE flapping spam Musa reported."""
    import json, time
    _frozen_green_reg(monkeypatch, agent="caiish")
    row = w.AgentCtx(agent="caiish", ctx_tokens=680_000, pct=68, level="green",
                     age_s=30, action="ok", stale=False)
    # A fresh frozen episode (paged False) but the operator was frozen-paged 20 min ago — well
    # inside the 12h re-page cooldown. Pane BUSY so _frozen_gauge_should_page would otherwise page.
    seed = {"__ctx_freeze__": {"caiish": {"tokens": row.ctx_tokens,
            "since": time.time() - (w._CTX_FROZEN_S + 600), "paged": False,
            "last_page_ts": time.time() - 20 * 60}}}
    (tmp_path / "state.json").write_text(json.dumps(seed))
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: False)  # BUSY -> would page if not cooled
    fired = w.run_alerts([row])
    assert sent == [], "a re-freeze within the re-page cooldown must NOT re-page (op#18830 flap fix)"
    assert fired == []


def test_frozen_repage_after_cooldown_pages_again(monkeypatch, tmp_path):
    """The cooldown DELAYS the re-page, never permanently swallows it: once _FROZEN_REPAGE_S has
    passed and the gauge is still frozen, the operator is told again."""
    import json, time
    _frozen_green_reg(monkeypatch, agent="caiish")
    row = w.AgentCtx(agent="caiish", ctx_tokens=680_000, pct=68, level="green",
                     age_s=30, action="ok", stale=False)
    seed = {"__ctx_freeze__": {"caiish": {"tokens": row.ctx_tokens,
            "since": time.time() - (w._CTX_FROZEN_S + 600), "paged": False,
            "last_page_ts": time.time() - (w._FROZEN_REPAGE_S + 3600)}}}  # cooldown expired
    (tmp_path / "state.json").write_text(json.dumps(seed))
    monkeypatch.setattr(w, "_STATE_FILE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(w, "_send_alert", lambda t: sent.append(t))
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: False)  # BUSY
    w.run_alerts([row])
    assert len(sent) == 1 and "FROZEN" in sent[0], "after the cooldown expires a still-frozen gauge re-pages"


def test_frozen_first_page_unaffected_by_cooldown(monkeypatch, tmp_path):
    """A first-ever frozen page (no prior last_page_ts) is NOT gated by the cooldown — the true
    positive still fires immediately."""
    import json, time
    _frozen_green_reg(monkeypatch, agent="hubish")
    row = w.AgentCtx(agent="hubish", ctx_tokens=610_000, pct=61, level="amber",
                     age_s=30, action="checkpoint-nudge", stale=False)
    sent = _seed_frozen(tmp_path, monkeypatch, row)  # seeds paged False, NO last_page_ts
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: None)  # cross-host amber -> pages via band arm
    w.run_alerts([row])
    assert len(sent) == 1 and "FROZEN" in sent[0], "first frozen page must fire regardless of cooldown"


def test_frozen_gauge_should_page_helper_matrix(monkeypatch):
    """Unit the decision seam directly across the (idle, band) matrix."""
    reg = {"tmux": "x", "host": "self", "alerts": True}
    green = w.AgentCtx(agent="g", ctx_tokens=130_000, pct=13, level="green", age_s=1, action="ok")
    amber = w.AgentCtx(agent="a", ctx_tokens=650_000, pct=65, level="amber", age_s=1, action="ok")
    red = w.AgentCtx(agent="r", ctx_tokens=850_000, pct=85, level="red", age_s=1, action="ok")
    # BUSY -> always page (any band)
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: False)
    assert w._frozen_gauge_should_page(green, reg) is True
    assert w._frozen_gauge_should_page(amber, reg) is True
    assert w._frozen_gauge_should_page(red, reg) is True
    # CONFIRMED IDLE -> suppress at ANY band (green AND amber AND red) — op#18837 root fix
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: True)
    assert w._frozen_gauge_should_page(green, reg) is False
    assert w._frozen_gauge_should_page(amber, reg) is False
    assert w._frozen_gauge_should_page(red, reg) is False
    # UNREACHABLE (cross-host) -> band arm: green suppress, amber+ page (the hub zombie)
    monkeypatch.setattr(w, "_agent_is_idle", lambda reg: None)
    assert w._frozen_gauge_should_page(green, reg) is False
    assert w._frozen_gauge_should_page(amber, reg) is True
    assert w._frozen_gauge_should_page(red, reg) is True


# ── CAI-1360 follow-up (bus 37018/37021): fix the STALE cross-host registry hosts ────────
# Verified at source 2026-09-03: cai runs LOCALLY on the Mini (host must be "self", not the DEAD
# unresolvable mac-studio); the hub is genuinely cross-host AND MOVING (VPS wingmen-core -> gzb),
# so it is marked cross_host_unreachable -> idle short-circuits to None with NO doomed ssh
# (Option B, move-resilient: encodes no host-specific ssh to rot). No entry may point at mac-studio.

def test_cai_registry_is_local_self_not_studio():
    """cai runs LOCALLY on the Mini (live local tmux `cai`, confirmed at source) — host must be
    'self' so its pane is captured locally (no ssh) and its idle-vs-active gate actually works.
    The bug: host='mac-studio' -> doomed `ssh Musa@mac-studio` -> None -> band-fallback false-page."""
    cai = w._AGENT_REGISTRY.get("cai")
    assert cai is not None
    assert cai["host"] == "self", "cai is local on the Mini; host must be 'self'"
    assert "studio" not in cai.get("label", "").lower(), "drop the stale (Studio) label"


def test_hub_registry_marked_cross_host_unreachable_no_dead_host():
    """The hub is genuinely cross-host and MOVING (VPS->gzb) — mark it cross_host_unreachable so
    idle short-circuits to None with no doomed ssh, encoding no host-specific ssh that will rot.
    The dead mac-studio host must not linger."""
    hub = w._AGENT_REGISTRY["cc-orchestrator"]
    assert hub.get("cross_host_unreachable") is True
    assert hub.get("host") != "mac-studio", "the dead mac-studio host must not linger on the hub"


def test_no_registry_entry_points_at_dead_mac_studio():
    """mac-studio is DEAD (unresolvable post-relocation 2026-07-31) — no real registry entry may
    point at it, else every cross-host probe for that body silently fails as a stale-config zombie."""
    stale = sorted(a for a, r in w._AGENT_REGISTRY.items() if r.get("host") == "mac-studio")
    assert stale == [], f"registry entries still on the dead mac-studio host: {stale}"


def test_cross_host_unreachable_idle_is_none_without_capture(monkeypatch):
    """A cross_host_unreachable body short-circuits _pane_state to unreachable/idle=None WITHOUT
    calling _capture_pane at all (Option B: no doomed cross-host ssh to a dead/moving host). The
    conftest live-seam guard makes _capture_pane/_tmux_run RAISE if reached, so a clean idle=None
    here proves the short-circuit fired before any transport."""
    reg = {"host": None, "tmux": "orch", "cross_host_unreachable": True, "label": "hub"}
    st = w._pane_state(reg)
    assert st.reachable is False and st.idle is None
    assert w._agent_is_idle(reg) is None


def test_cross_host_unreachable_frozen_amber_still_pages():
    """Band-fallback preserved: a cross_host_unreachable body with a frozen gauge at amber still
    PAGES (idle=None -> value band), green still suppresses — via the short-circuit, no ssh (a
    reached _capture_pane/_tmux_run would trip the conftest live-seam guard and error this test)."""
    reg = {"host": None, "tmux": "orch", "cross_host_unreachable": True,
           "external_recycle": True, "alerts": True, "label": "hub"}
    amber = _ctx(agent="cc-orchestrator", pct=62, level="amber")
    green = _ctx(agent="cc-orchestrator", pct=30, level="green")
    assert w._frozen_gauge_should_page(amber, reg) is True, "unreachable+amber frozen -> band-fallback page"
    assert w._frozen_gauge_should_page(green, reg) is False, "unreachable+green -> nothing high hidden -> suppress"


def test_short_circuit_is_narrow_normal_body_still_captured(monkeypatch):
    """Regression guard: the cross_host_unreachable short-circuit must be NARROW — a normal body
    (no such flag, e.g. cai=self now) still goes through _capture_pane, keeping real idle detection
    (that is the whole point of relocating cai to 'self')."""
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: PANE_IDLE_AUTHED)
    reg = {"host": "self", "tmux": "x", "label": "local-body"}  # NO cross_host_unreachable
    st = w._pane_state(reg)
    assert st.reachable is True and st.idle is True, "a normal body must NOT be short-circuited"


def test_cross_host_unreachable_newest_handoff_makes_no_ssh(monkeypatch):
    """_newest_handoff for a cross_host_unreachable body returns None WITHOUT ssh — otherwise host=None
    makes it a doomed `ssh Musa@None` on every --json/plan run (the moving-host rot Option B avoids)."""
    calls = []
    def _record(argv, *a, **k):
        calls.append(argv)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(w.subprocess, "run", _record)
    reg = {"host": None, "tmux": "orch", "cross_host_unreachable": True,
           "handoff_glob": "reports/*.md", "handoff_dir": "~/x", "label": "hub"}
    assert w._newest_handoff(reg) is None
    assert calls == [], f"no ssh for a cross_host_unreachable handoff lookup: {calls}"
