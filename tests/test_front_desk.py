"""front_desk.py pure-logic tests (bus #63383): classify()'s double-guard
(regex AND cos_triage, never answer on a maybe), the irsyad exclusion filter
(op#27563 — irsyad status never goes to Musa), and render_live_list()'s
triage_summary-only / no-raw-text formatting."""
from __future__ import annotations

from nervous_system import front_desk as fd
from nervous_system import triage


def _triage(category=triage.DIRECT_ANSWER, domain="fleet"):
    return {"category": category, "domain": domain}


# ── classify() ───────────────────────────────────────────────────────────────

def test_classify_answer_on_live_list_with_direct_answer_triage():
    assert fd.classify("live list", _triage()) == fd.ANSWER


def test_classify_answer_case_insensitive_and_hyphenated_variant():
    assert fd.classify("Live-List please", _triage()) == fd.ANSWER


def test_classify_answer_on_pending_on_me_phrasing():
    assert fd.classify("what's pending on me?", _triage()) == fd.ANSWER


def test_classify_ack_when_text_does_not_match_whitelist():
    assert fd.classify("can you ship the storefront PR today", _triage()) == fd.ACK


def test_classify_ack_when_regex_matches_but_triage_disagrees():
    # Double-guard: a regex hit alone is not enough if cos_triage says this
    # isn't a direct-answer (e.g. it mis-threaded into a governance fork).
    bad = _triage(category=triage.GOVERNANCE_FORK)
    assert fd.classify("live list", bad) == fd.ACK


def test_classify_ack_when_domain_is_irsyad_even_with_direct_answer_category():
    bad = _triage(category=triage.DIRECT_ANSWER, domain="irsyad")
    assert fd.classify("live list", bad) == fd.ACK


def test_classify_ack_on_empty_text():
    assert fd.classify("", _triage()) == fd.ACK
    assert fd.classify(None, _triage()) == fd.ACK


def test_classify_ack_when_no_triage_given():
    assert fd.classify("live list", None) == fd.ACK


# ── is_irsyad_row / filter_irsyad ───────────────────────────────────────────

def test_is_irsyad_row_true_on_ask_text():
    assert fd.is_irsyad_row({"ask": "chase the irsyad receipt numbering decision"})


def test_is_irsyad_row_true_on_triage_summary():
    assert fd.is_irsyad_row({"triage_summary": "gazzabyte onboarding blocked on PDPA"})


def test_is_irsyad_row_true_on_delegated_to():
    assert fd.is_irsyad_row({"delegated_to": "cc-irsyad-coord", "ask": "generic ask text"})


def test_is_irsyad_row_false_on_unrelated_row():
    assert not fd.is_irsyad_row({"ask": "ship cosem exams export", "delegated_to": "cc-cosem-tdu-coord"})


def test_is_irsyad_row_handles_missing_fields():
    assert not fd.is_irsyad_row({})


def test_filter_irsyad_drops_only_irsyad_rows():
    rows = [
        {"id": 1, "ask": "ship cosem exams export"},
        {"id": 2, "ask": "irsyad receipt numbering"},
        {"id": 3, "delegated_to": "cc-irsyad-coord"},
        {"id": 4, "ask": "fix the deploy gate"},
    ]
    kept = fd.filter_irsyad(rows)
    assert [r["id"] for r in kept] == [1, 4]


# ── render_live_list() ───────────────────────────────────────────────────────

def test_render_empty_rows():
    assert "Nothing open" in fd.render_live_list([])


def test_render_waiting_on_you_listed_first():
    rows = [
        {"id": 5, "triage_summary": "in progress elsewhere", "waiting_on_operator": False, "delegated_to": "cc-cosem"},
        {"id": 6, "triage_summary": "needs your call", "waiting_on_operator": True, "delegated_to": None},
    ]
    out = fd.render_live_list(rows)
    assert out.index("#6") < out.index("#5")
    assert "waiting on you" in out.lower()


def test_render_uses_triage_summary_not_raw_ask():
    rows = [{"id": 7, "ask": "RAW TEXT THAT MUST NOT APPEAR", "triage_summary": "distilled summary",
             "waiting_on_operator": False, "delegated_to": "x"}]
    out = fd.render_live_list(rows)
    assert "distilled summary" in out
    assert "RAW TEXT THAT MUST NOT APPEAR" not in out


def test_render_missing_delegated_to_shows_unassigned():
    rows = [{"id": 8, "triage_summary": "no owner yet", "waiting_on_operator": False, "delegated_to": None}]
    out = fd.render_live_list(rows)
    assert "unassigned" in out
