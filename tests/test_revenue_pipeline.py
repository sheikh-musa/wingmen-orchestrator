"""Unit tests for nervous_system/revenue_pipeline — the Head of Revenue Phase 1
read-only digest generator. Covers stage grouping + digest formatting + the
"what needs you" derivation. No DB; pure functions over dict rows."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nervous_system.revenue_pipeline import (  # noqa: E402
    STAGE_ORDER,
    group_by_stage,
    needs_you,
    render_digest,
)

TODAY = date(2026, 7, 22)


def _rows():
    return [
        {
            "slug": "gazzabyte-partner", "name": "Gazzabyte", "stage": "closing",
            "value_model": "retainer", "est_value": None, "currency": "SGD",
            "next_action": "Draft the partner payment structure.",
            "next_action_owner": "Nazim", "due": "2026-09-30", "owner": "operator",
            "blocker": "Operator to decide terms.", "operator_is_blocker": True,
            "gate_status": "open", "gate_note": "cai — GIRO money-audit",
            "partner": "Gazzabyte", "sort_priority": 1, "notes": None,
        },
        {
            "slug": "shipforge", "name": "shipforge", "stage": "scoped",
            "value_model": "outcome-billed", "est_value": None, "currency": "SGD",
            "next_action": "Close the render leak.", "next_action_owner": "cc-shipforge",
            "due": None, "owner": "operator", "blocker": "Render leak.",
            "operator_is_blocker": False, "gate_status": "none", "gate_note": None,
            "partner": "Desmond", "sort_priority": 2, "notes": None,
        },
        {
            "slug": "storefront", "name": "storefront", "stage": "scoped",
            "value_model": "saas", "est_value": None, "currency": "SGD",
            "next_action": "First paying merchant.", "next_action_owner": "cc-storefront",
            "due": None, "owner": "operator", "blocker": "Identity bridge.",
            "operator_is_blocker": False, "gate_status": "none", "gate_note": None,
            "partner": None, "sort_priority": 4, "notes": None,
        },
        {
            "slug": "desmond", "name": "Desmond Shen", "stage": "channel",
            "value_model": "partner", "est_value": None, "currency": "SGD",
            "next_action": "Track his clients.", "next_action_owner": "cc-orch",
            "due": None, "owner": "operator", "blocker": "Not a sale.",
            "operator_is_blocker": False, "gate_status": "n/a", "gate_note": None,
            "partner": "Desmond", "sort_priority": 9, "notes": None,
        },
    ]


# ----- group_by_stage -------------------------------------------------------

def test_grouping_orders_stages_by_pipeline():
    grouped = group_by_stage(_rows())
    stages = [s for s, _ in grouped]
    # scoped before closing? No — STAGE_ORDER puts scoped before closing.
    assert stages == ["scoped", "closing", "channel"]
    for s in stages:
        assert s in STAGE_ORDER


def test_grouping_sorts_within_stage_by_priority():
    grouped = dict(group_by_stage(_rows()))
    scoped = grouped["scoped"]
    assert [o["slug"] for o in scoped] == ["shipforge", "storefront"]  # 2 before 4


def test_grouping_keeps_unknown_stage_not_dropped():
    rows = _rows() + [{"slug": "weird", "name": "Weird", "stage": "renegotiation"}]
    grouped = dict(group_by_stage(rows))
    assert "renegotiation" in grouped
    assert sum(len(v) for v in grouped.values()) == len(rows)


def test_grouping_empty():
    assert group_by_stage([]) == []


# ----- needs_you ------------------------------------------------------------

def test_needs_you_flags_operator_blocker_and_open_gate():
    ny = needs_you(_rows(), TODAY)
    slugs = {o["slug"] for o in ny}
    # Gazzabyte: operator_is_blocker + open gate + due-soon -> in.
    assert "gazzabyte-partner" in slugs
    # shipforge/storefront/desmond: none of the triggers -> out.
    assert "shipforge" not in slugs
    assert "desmond" not in slugs


def test_needs_you_flags_due_soon_even_without_blocker():
    rows = [{
        "slug": "x", "name": "X", "stage": "scoped", "operator_is_blocker": False,
        "gate_status": "none", "due": "2026-07-31", "sort_priority": 1,
    }]
    assert [o["slug"] for o in needs_you(rows, TODAY)] == ["x"]


def test_needs_you_ignores_far_future_due():
    rows = [{
        "slug": "x", "name": "X", "stage": "lead", "operator_is_blocker": False,
        "gate_status": "none", "due": "2027-12-31", "sort_priority": 1,
    }]
    assert needs_you(rows, TODAY) == []


def test_needs_you_excludes_delivered_and_lost():
    rows = [
        {"slug": "d", "name": "D", "stage": "delivered", "operator_is_blocker": True},
        {"slug": "l", "name": "L", "stage": "lost", "operator_is_blocker": True},
    ]
    assert needs_you(rows, TODAY) == []


# ----- render_digest --------------------------------------------------------

def test_digest_has_both_sections_and_header():
    out = render_digest(_rows(), TODAY)
    assert "# Revenue pipeline — week of 2026-07-22" in out
    assert "## Pipeline by stage" in out
    assert "## What needs you this week" in out
    assert "4 threads tracked" in out


def test_digest_marks_operator_blocker():
    out = render_digest(_rows(), TODAY)
    assert "Gazzabyte" in out
    assert "⚠️ you" in out  # operator-blocker marker in the stage listing


def test_digest_needs_you_lists_gazzabyte_with_reasons():
    out = render_digest(_rows(), TODAY)
    section = out.split("## What needs you this week", 1)[1]
    assert "Gazzabyte" in section
    assert "you are the blocker" in section
    assert "open money/residency gate" in section
    assert "due 2026-09-30" in section


def test_digest_needs_you_empty_message():
    calm = [{
        "slug": "s", "name": "S", "stage": "scoped", "operator_is_blocker": False,
        "gate_status": "none", "due": None, "value_model": "saas", "sort_priority": 1,
    }]
    out = render_digest(calm, TODAY)
    assert "Nothing is blocked on you right now." in out


def test_digest_handles_null_value_model():
    rows = [{"slug": "n", "name": "N", "stage": "lead", "value_model": None,
             "est_value": None, "operator_is_blocker": False, "gate_status": "none"}]
    out = render_digest(rows, TODAY)
    assert "value TBD" in out


def test_digest_renders_est_value_when_present():
    rows = [{"slug": "n", "name": "N", "stage": "priced", "value_model": "retainer",
             "est_value": 12000, "currency": "SGD", "operator_is_blocker": False,
             "gate_status": "none"}]
    out = render_digest(rows, TODAY)
    assert "~12,000 SGD" in out
