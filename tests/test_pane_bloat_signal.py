"""Tests for scripts/lib/pane_bloat_signal.py — the fresh per-pane bloat gauge.

The load-bearing PURE parts get unit tests: parse_hint_k (the regex that reads CC's
`/clear to save {N}k tokens` hint) and is_bloated_k (the fail-closed threshold). The
tmux capture path is a thin read-only subprocess wrapper (not unit-tested here — it
needs a live pane; exercised by the observe run).

Fixtures are REAL pane tails captured live on 2026-08-15 (op#13050 verification).
"""
import pytest

from scripts.lib import pane_bloat_signal as pbs


# Real caai idle tail (context high — hint present, even with staged composer text).
CAAI_TAIL = """\
  nothing to drive until it lands. Standing ready.
✻ Baked for 58s · 1 shell still running
                                           new task? /clear to save 551k tokens
────────────────────────────────────────────────────────────────────────────────
❯ [wake] new inbox item — read your agent_messages inbox and act
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on · 1 shell · ← for agents · ↓ to manage
"""

# Real storefront idle tail (context below CC's nudge bar — NO hint).
STOREFRONT_TAIL = """\
  I'll pick those up next wake. Inbox 0 unread → idle.
✻ Cogitated for 6m 46s
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
"""


# ── parse_hint_k ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    (CAAI_TAIL, 551.0),
    ("new task? /clear to save 795.9k tokens", 795.9),
    ("new task? /clear to save 437.2k tokens", 437.2),
    ("  /clear to save 454k tokens", 454.0),
    ("/clear to save 713.1k tokens", 713.1),
])
def test_parse_hint_k_present(text, expected):
    assert pbs.parse_hint_k(text) == expected


@pytest.mark.parametrize("text", [
    STOREFRONT_TAIL,          # real idle pane, context below the nudge bar
    "",
    None,
    "❯ just a composer, no hint",
    "/clear to save the day",  # the words but not the token pattern
])
def test_parse_hint_k_absent_is_none(text):
    assert pbs.parse_hint_k(text) is None


def test_parse_hint_k_takes_last_match():
    # An older hint up in scrollback + a fresher one lower down -> freshest wins.
    txt = "old: /clear to save 900k tokens\n...\nnow: /clear to save 120k tokens"
    assert pbs.parse_hint_k(txt) == 120.0


# ── is_bloated_k (fail-closed threshold) ─────────────────────────────────────
def test_is_bloated_at_and_above_fire_bar():
    assert pbs.is_bloated_k(850.0, fire_k=850) is True
    assert pbs.is_bloated_k(795.9, fire_k=850) is False   # high but below bar
    assert pbs.is_bloated_k(900.0, fire_k=850) is True


def test_is_bloated_none_fails_closed():
    # No hint / capture failed -> NEVER treated as bloated.
    assert pbs.is_bloated_k(None, fire_k=850) is False
