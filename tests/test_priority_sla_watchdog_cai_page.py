"""Tests for priority_sla_watchdog.build_page — the cai (governance node)
special-case page-template.

cai is the singleton governance node carrying ~55% of fleet rulings with NO
failover. A stalled/absent cai must page DISTINCTLY from a routine stuck engineer
lane: the operator has to read "the fleet's ruling path is down", not "a lane is
slow". These tests pin that the cai case (a) contains the governance-stall
wording, (b) differs from the generic page, and (c) leaves every non-cai page
byte-identical to the pre-change template.
"""
from __future__ import annotations

from scripts.priority_sla_watchdog import build_page


def _violation(agent: str) -> dict:
    return {
        "agent": agent,
        "message_id": 424242,
        "priority": "P1",
        "from_agent": "cc-orchestrator",
        "subject": "gate request: irsyad GIRO upload residency",
        "violation_type": "unresponded",
        "elapsed_minutes": 33,
        "threshold_minutes": 20,
    }


def test_cai_page_reads_as_governance_stall():
    page = build_page(_violation("cai"), nudges=1)
    # governance-stall headline + the required "unqueueable" / ruling-path wording
    assert "GOVERNANCE QUEUE STALLED" in page
    assert "no live cai ruling path" in page
    assert "unqueueable" in page
    # explicitly NOT framed as a routine stuck lane
    assert "not responding" not in page


def test_cai_page_differs_from_generic_page():
    cai_page = build_page(_violation("cai"), nudges=1)
    generic_page = build_page(_violation("cc-scholar"), nudges=1)
    assert cai_page != generic_page
    # the governance framing is unique to the cai page
    assert "GOVERNANCE QUEUE STALLED" not in generic_page


def test_non_cai_page_unchanged():
    """Every non-cai page must keep the exact pre-change generic template."""
    v = _violation("cc-scholar")
    page = build_page(v, nudges=2)
    expected = (
        "\U0001F6A8 P1 bus message stuck — cc-scholar not responding\n"
        "\n"
        "What: P1 message #424242 from cc-orchestrator to cc-scholar has been "
        "unresponded for 33 min and a re-nudge did not clear it.\n"
        "Why it matters: P0/P1 is time-sensitive — an unhandled one silently "
        "stalls the fleet (this is the class that cost ~2h on a go-live).\n"
        "What to do: Open cc-scholar's session and action msg #424242, or reassign it.\n"
        "\n"
        "Detail: agent=cc-scholar msg_id=424242 type=unresponded elapsed=33m "
        "prior_nudges=2 subject='gate request: irsyad GIRO upload residency'\n"
        "Ref: PRIORITY-SLA-WATCHDOG"
    )
    assert page == expected
