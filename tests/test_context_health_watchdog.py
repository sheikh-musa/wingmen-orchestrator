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
