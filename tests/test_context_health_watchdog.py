"""Tests for scripts.context_health_watchdog — the ARMED reset executor + gates.

Covers the safety-critical decision logic that auto-manipulates LIVE singleton
agents via cross-host tmux (op#5516 follow-up). We mock the pane-capture / tmux /
handoff-freshness boundaries so nothing here touches a real agent.

Explicitly NOT tested with --arm against a live body — every side-effecting call
(_capture_pane, _send_literal, _send_key, _newest_handoff, _page_loud) is patched.
"""
from __future__ import annotations

import time

import pytest

from scripts import context_health_watchdog as w


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #

def _ctx(agent="cc-orchestrator", pct=85, level="red", action="reset-eligible"):
    return w.AgentCtx(agent=agent, ctx_tokens=int(pct / 100 * 1_000_000),
                      pct=pct, level=level, age_s=30, action=action)


REG = w._AGENT_REGISTRY["cc-orchestrator"]  # remote (mac-studio), auto_reset=True

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
                        lambda reg: w.PaneState(True, True, True, "", ""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)  # force a checkpoint
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "fresh handoff x.md"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK"))[1])

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
                        lambda a, reg: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK (handoff x)"))[1])

    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")])
    assert calls["reset"] == 1
    assert "red reset OK" in res[0]


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
                        lambda reg: w.PaneState(True, True, True, "", ""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)  # force a checkpoint attempt
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "fresh handoff x.md"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK"))[1])

    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="amber")
    assert calls["reset"] == 0        # THE invariant: amber NEVER /clear-resets a red body
    assert calls["checkpoint"] == 1   # but it DOES write-only checkpoint it (≥SOFT)
    assert "WOULD reset" in res[0] and "UNARMED" in res[0]


def test_arm_amber_amber_body_checkpoints(monkeypatch, tmp_path):
    """arm=amber on an amber body: DOES invoke the checkpoint path, never reset."""
    calls = {"checkpoint": 0, "reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(True, True, True, "", ""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)
    monkeypatch.setattr(w, "_do_checkpoint",
                        lambda a, reg, st: (calls.__setitem__("checkpoint", calls["checkpoint"] + 1), (True, "fresh handoff x.md"))[1])
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK"))[1])

    res = w.run_executor([_ctx(pct=70, level="amber", action="checkpoint-nudge")], arm_level="amber")
    assert calls["checkpoint"] == 1
    assert calls["reset"] == 0
    assert "amber checkpoint OK" in res[0]


def test_arm_red_red_body_resets(monkeypatch, tmp_path):
    """arm=red on a RED body: the destructive reset path IS reachable."""
    calls = {"reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg: (calls.__setitem__("reset", calls["reset"] + 1), (True, "reset OK (handoff x)"))[1])
    res = w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="red")
    assert calls["reset"] == 1
    assert "red reset OK" in res[0]


def test_arm_off_is_a_noop(monkeypatch, tmp_path):
    """arm=off never executes anything, even for a red body."""
    calls = {"n": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_reset", lambda a, reg: (calls.__setitem__("n", calls["n"] + 1), (True, "x"))[1])
    monkeypatch.setattr(w, "_do_checkpoint", lambda a, reg, st: (calls.__setitem__("n", calls["n"] + 1), (True, "x"))[1])
    assert w.run_executor([_ctx(pct=88, level="red", action="reset-eligible")], arm_level="off") == []
    assert calls["n"] == 0


def test_plan_arm_amber_red_body_dry_run(monkeypatch):
    """The PRINTED plan for a red body under arm=amber is a dry-run 'WOULD reset',
    and it does NOT probe the live pane (off/amber never touches the agent)."""
    monkeypatch.setattr(w, "_agent_is_idle",
                        lambda reg: pytest.fail("planner probed a live pane under arm=amber"))
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg: pytest.fail("planner executed a reset under arm=amber"))
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
                        lambda a, reg: (calls.__setitem__("n", calls["n"] + 1), (True, "x"))[1])
    monkeypatch.setattr(w, "_do_checkpoint", lambda a, reg, st: (True, "x"))

    res = w.run_executor([_ctx(agent="orch-console", pct=90, level="red", action="reset-eligible")])
    assert calls["n"] == 0  # auto_reset=False -> skipped entirely
    assert res == []


def test_run_executor_red_dedup(monkeypatch, tmp_path):
    """A body reset last cycle is not reset again while the dedup window holds."""
    calls = {"reset": 0}
    monkeypatch.setattr(w, "_EXEC_STATE_FILE", tmp_path / "exec.json")
    monkeypatch.setattr(w, "_do_reset",
                        lambda a, reg: (calls.__setitem__("reset", calls["reset"] + 1), (True, "x"))[1])
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

    def _boom(a, reg):
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
                        lambda reg: w.PaneState(True, False, True, "", ""))
    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "not idle" in detail


def test_do_reset_skips_unauthenticated(monkeypatch):
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(True, True, False, "", ""))
    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "authenticated" in detail


def test_do_reset_aborts_when_checkpoint_fails(monkeypatch):
    """No fresh handoff + checkpoint can't produce one -> ABORT, never /clear."""
    paged = []
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(True, True, True, "", ""))
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
                        lambda reg: w.PaneState(True, True, True, "", ""))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: keys.append(k) or True)
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "empty prompt, no clear typed\n")
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))
    monkeypatch.setattr(w.time, "sleep", lambda s: None)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "phantom-guard" in detail
    assert "Enter" not in keys  # never blind-submit
    assert paged and "phantom-guard FAILED" in paged[0]


def test_do_reset_full_happy_path(monkeypatch):
    """idle+authed+fresh-handoff -> /clear lands -> auth holds -> boot sent -> OK."""
    keys, literals = [], []
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(True, True, True, "", "reading boot_briefing"))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: literals.append(t) or True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: keys.append(k) or True)
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "│ > /clear │\n")  # /clear present
    monkeypatch.setattr(w.time, "sleep", lambda s: None)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is True and "reset OK" in detail
    assert "/clear" in literals
    assert keys.count("Enter") >= 2  # /clear submit + boot submit


def test_do_reset_auth_broke_after_clear(monkeypatch):
    """If auth breaks after /clear, page LOUDLY and stop (no boot)."""
    paged = []
    states = iter([
        w.PaneState(True, True, True, "", ""),   # initial gate
        w.PaneState(True, True, True, "", ""),   # re-verify before clear
        w.PaneState(True, True, False, "", "Select login method"),  # after clear: auth broke
    ])
    monkeypatch.setattr(w, "_pane_state", lambda reg: next(states))
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)
    monkeypatch.setattr(w, "_capture_pane", lambda reg, lines=40: "│ > /clear │\n")
    monkeypatch.setattr(w, "_page_loud", lambda text: paged.append(text))
    monkeypatch.setattr(w.time, "sleep", lambda s: None)

    ok, detail = w._do_reset(_ctx(), REG)
    assert ok is False and "auth broke" in detail
    assert paged and "auth BROKE" in paged[0]


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
    st = w.PaneState(True, True, True, "", "")
    ok, detail = w._do_checkpoint(_ctx(), REG, st)
    assert ok is True and "session-handoff-new.md" in detail


def test_do_checkpoint_times_out_no_handoff(monkeypatch):
    monkeypatch.setattr(w, "_send_literal", lambda reg, t: True)
    monkeypatch.setattr(w, "_send_key", lambda reg, k: True)
    monkeypatch.setattr(w, "_preserve_input_box", lambda reg, st: "")
    monkeypatch.setattr(w, "_newest_handoff", lambda reg: None)  # never appears
    monkeypatch.setattr(w, "_CHECKPOINT_WAIT_S", 0)  # no waiting
    st = w.PaneState(True, True, True, "", "")
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
                        lambda a, reg: pytest.fail("planner executed in DRY-RUN"))
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
                        lambda reg: w.PaneState(True, True, True, "", ""))
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
    st = w.PaneState(True, True, True, "", "")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is True and "verified" in detail


def test_verify_capture_fails_no_handoff(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: None)
    st = w.PaneState(True, True, True, "", "")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False and "handoff" in detail  # state NOT saved -> refuse


def test_verify_capture_fails_inbox_not_drained(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 1)  # arrived mid-checkpoint
    st = w.PaneState(True, True, True, "", "")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False and "drained" in detail


def test_verify_capture_fails_indeterminate_inbox(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: None)  # can't re-verify
    st = w.PaneState(True, True, True, "", "")
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False


def test_verify_capture_fails_unsent_input_present(monkeypatch):
    monkeypatch.setattr(w, "_fresh_handoff", lambda reg: "session-handoff-now.md")
    monkeypatch.setattr(w, "_unhandled_operator_count", lambda reg: 0)
    st = w.PaneState(True, True, True, "still typing this", "")  # unsent text remains
    ok, detail = w._verify_capture_before_clear(REG, st)
    assert ok is False and "input-box" in detail


def test_do_reset_aborts_when_capture_unverified(monkeypatch):
    """If capture cannot be VERIFIED before /clear -> ABORT loudly, never /clear."""
    paged, cleared = [], []
    monkeypatch.setattr(w, "_pane_state",
                        lambda reg: w.PaneState(True, True, True, "", ""))
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
