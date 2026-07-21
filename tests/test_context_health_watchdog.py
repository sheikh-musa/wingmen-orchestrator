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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
