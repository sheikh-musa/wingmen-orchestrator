"""Acceptance test for the idle-with-work watchdog core (orch-console #48417).

Replays today's incident: cc-irsyad-coord idle while owning ready work (#114/#119/#129)
-> MUST fire. A lane whose only items carry blocked_on -> MUST NOT fire. Pure core, no DB.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import idle_with_work_watchdog as w  # noqa: E402


def _item(ref, blocked_on=None):
    return {"ref": ref, "summary": ref, "blocked_on": blocked_on}


COORD_OPEN = [_item("commitment#114"), _item("commitment#119"), _item("commitment#129")]


def test_replay_coord_idle_with_open_work_fires():
    c = w.classify(COORD_OPEN, pane_idle=True)
    assert c["should_fire"] is True
    assert len(c["actionable"]) == 3 and c["blocked"] == []


def test_only_blocked_items_does_not_fire():
    items = [_item("commitment#200", blocked_on="waiting on Wan since 2026-10-01"),
             _item("commitment#201", blocked_on="client review (Shuq)")]
    c = w.classify(items, pane_idle=True)
    assert c["should_fire"] is False
    assert c["actionable"] == [] and len(c["blocked"]) == 2


def test_not_idle_does_not_fire():
    assert w.classify(COORD_OPEN, pane_idle=False)["should_fire"] is False


def test_mixed_fires_on_actionable_subset():
    items = COORD_OPEN + [_item("commitment#210", blocked_on="external: bank batch")]
    c = w.classify(items, pane_idle=True)
    assert c["should_fire"] is True
    assert len(c["actionable"]) == 3 and len(c["blocked"]) == 1


def test_empty_blocked_on_is_actionable():
    # a blocked_on of "" or whitespace is NOT a real block
    c = w.classify([_item("commitment#1", blocked_on="   ")], pane_idle=True)
    assert c["should_fire"] is True and len(c["actionable"]) == 1


def test_items_hash_is_order_stable():
    assert w.items_hash(COORD_OPEN) == w.items_hash(list(reversed(COORD_OPEN)))
    assert w.items_hash(COORD_OPEN) != w.items_hash(COORD_OPEN[:2])
