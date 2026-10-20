"""Pure-logic tests for scripts/reconcile_responded_at.py (held_commitment #446).

Covers only the matching/classification logic (no DB). WHY these three tiers and
not a fuzzy "subject proximity" fourth tier: a blind/fuzzy match risks exactly the
false-stamp failure mode the design explicitly forbids (never mask a genuine drop).
Tier precedence: commitment > thread > backreference (most-specific-signal first).
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "reconcile_responded_at.py")
_spec = importlib.util.spec_from_file_location("reconcile_responded_at", _PATH)
rra = importlib.util.module_from_spec(_spec)
sys.modules["reconcile_responded_at"] = rra
_spec.loader.exec_module(rra)

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def _row(id, thread_id="t1", from_agent="orch-console", to_agent="cc-fleet-health",
          subject="ask", body="", priority="P2", created_at=T0, **kw):
    r = dict(id=id, thread_id=thread_id, from_agent=from_agent, to_agent=to_agent,
             subject=subject, body=body, priority=priority, created_at=created_at)
    r.update(kw)
    return r


# ---- parse_commitment_id ----

def test_parse_commitment_id_extracts_number():
    assert rra.parse_commitment_id("COMMITMENT DUE #446: BUILD: foo") == 446


def test_parse_commitment_id_none_when_absent():
    assert rra.parse_commitment_id("just a regular subject") is None


# ---- match_via_thread ----

def test_thread_match_finds_later_reply_from_recipient_same_thread():
    candidate = _row(1, thread_id="t1", to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(2, thread_id="t1", from_agent="cc-cosem-exams", created_at=T0 + timedelta(minutes=5))
    idx = rra.index_replies_by_thread([reply])
    m = rra.match_via_thread(candidate, idx)
    assert m is not None
    assert m.tier == "thread"
    assert m.response_ref == "2"


def test_thread_match_ignores_reply_before_candidate():
    candidate = _row(1, thread_id="t1", to_agent="cc-cosem-exams", created_at=T0)
    earlier_reply = _row(2, thread_id="t1", from_agent="cc-cosem-exams", created_at=T0 - timedelta(minutes=5))
    idx = rra.index_replies_by_thread([earlier_reply])
    assert rra.match_via_thread(candidate, idx) is None


def test_thread_match_ignores_reply_from_different_agent():
    candidate = _row(1, thread_id="t1", to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(2, thread_id="t1", from_agent="someone-else", created_at=T0 + timedelta(minutes=5))
    idx = rra.index_replies_by_thread([reply])
    assert rra.match_via_thread(candidate, idx) is None


def test_thread_match_ignores_reply_in_different_thread():
    candidate = _row(1, thread_id="t1", to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(2, thread_id="t2", from_agent="cc-cosem-exams", created_at=T0 + timedelta(minutes=5))
    idx = rra.index_replies_by_thread([reply])
    assert rra.match_via_thread(candidate, idx) is None


def test_thread_match_picks_earliest_qualifying_reply():
    candidate = _row(1, thread_id="t1", to_agent="cc-cosem-exams", created_at=T0)
    later = _row(3, thread_id="t1", from_agent="cc-cosem-exams", created_at=T0 + timedelta(minutes=20))
    earlier = _row(2, thread_id="t1", from_agent="cc-cosem-exams", created_at=T0 + timedelta(minutes=5))
    idx = rra.index_replies_by_thread([later, earlier])
    m = rra.match_via_thread(candidate, idx)
    assert m.response_ref == "2"


# ---- match_via_backreference ----

def test_backreference_match_on_explicit_id_in_subject():
    candidate = _row(42, to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(99, thread_id="other-thread", from_agent="cc-cosem-exams",
                 subject="Reconciled — closing #42", created_at=T0 + timedelta(hours=1))
    idx = rra.index_backreferences([reply])
    m = rra.match_via_backreference(candidate, idx)
    assert m is not None
    assert m.tier == "backref"
    assert m.response_ref == "99"


def test_backreference_does_not_match_substring_of_a_longer_number():
    candidate = _row(42, to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(99, from_agent="cc-cosem-exams", subject="see #4200 for context",
                 created_at=T0 + timedelta(hours=1))
    idx = rra.index_backreferences([reply])
    assert rra.match_via_backreference(candidate, idx) is None


def test_backreference_ignores_reply_from_wrong_agent():
    candidate = _row(42, to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(99, from_agent="someone-else", subject="closing #42",
                 created_at=T0 + timedelta(hours=1))
    idx = rra.index_backreferences([reply])
    assert rra.match_via_backreference(candidate, idx) is None


def test_backreference_ignores_reply_before_candidate():
    candidate = _row(42, to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(99, from_agent="cc-cosem-exams", subject="closing #42",
                 created_at=T0 - timedelta(hours=1))
    idx = rra.index_backreferences([reply])
    assert rra.match_via_backreference(candidate, idx) is None


# ---- match_via_commitment ----

def test_commitment_match_when_discharged():
    candidate = _row(5, from_agent="commitment-sweeper", to_agent="cc-fleet-health",
                      subject="COMMITMENT DUE #446: BUILD: foo", created_at=T0)
    commitments = {446: {"discharged_at": T0 + timedelta(hours=2), "discharged_by": "cc-fleet-health"}}
    m = rra.match_via_commitment(candidate, commitments)
    assert m is not None
    assert m.tier == "commitment"
    assert "446" in m.response_ref


def test_commitment_no_match_when_not_discharged():
    candidate = _row(5, from_agent="commitment-sweeper",
                      subject="COMMITMENT DUE #446: BUILD: foo", created_at=T0)
    commitments = {446: {"discharged_at": None, "discharged_by": None}}
    assert rra.match_via_commitment(candidate, commitments) is None


def test_commitment_no_match_for_non_sweeper_sender():
    candidate = _row(5, from_agent="orch-console",
                      subject="COMMITMENT DUE #446: BUILD: foo", created_at=T0)
    commitments = {446: {"discharged_at": T0, "discharged_by": "x"}}
    assert rra.match_via_commitment(candidate, commitments) is None


# ---- classify ----

def test_classify_splits_matched_pageable_stale_and_triage():
    now = T0 + timedelta(days=30)
    matched_candidate = _row(1, thread_id="t1", to_agent="cc-cosem-exams", created_at=T0)
    reply = _row(2, thread_id="t1", from_agent="cc-cosem-exams", created_at=T0 + timedelta(minutes=5))
    dropped_p1 = _row(10, to_agent="cc-quality", priority="P1", created_at=now - timedelta(days=1))
    ancient_p1 = _row(13, to_agent="cc-quality", priority="P1", created_at=T0)
    fresh_p1 = _row(11, to_agent="cc-quality", priority="P1", created_at=now - timedelta(minutes=5))
    low_prio_unmatched = _row(12, to_agent="cc-quality", priority="P3", created_at=T0)

    result = rra.classify(
        [matched_candidate, dropped_p1, ancient_p1, fresh_p1, low_prio_unmatched],
        thread_index=rra.index_replies_by_thread([reply]),
        backref_index=rra.index_backreferences([]),
        commitments={},
        now=now,
        dropped_min_age=timedelta(hours=6),
        pageable_max_age=timedelta(days=14),
    )
    assert [m.candidate_id for m in result["matches"]] == [1]
    assert [c["id"] for c in result["to_page"]] == [10]
    assert [c["id"] for c in result["stale_backlog"]] == [13]
    assert [c["id"] for c in result["needs_triage"]] == [11, 12]


# ---- coalesce + page body ----

def test_coalesce_by_recipient_groups_rows():
    rows = [_row(1, to_agent="a"), _row(2, to_agent="b"), _row(3, to_agent="a")]
    grouped = rra.coalesce_by_recipient(rows)
    assert set(grouped.keys()) == {"a", "b"}
    assert [r["id"] for r in grouped["a"]] == [1, 3]


def test_build_page_body_includes_priority_breakdown_and_ids():
    rows = [
        _row(1, to_agent="cc-quality", priority="P1", created_at=T0, subject="ask one"),
        _row(2, to_agent="cc-quality", priority="P1", created_at=T0, subject="ask two"),
        _row(3, to_agent="cc-quality", priority="P0", created_at=T0, subject="ask three"),
    ]
    subject, body = rra.build_page_body("cc-quality", rows, now=T0 + timedelta(days=2))
    assert "P0:1" in subject and "P1:2" in subject
    assert "#1" in body and "#2" in body and "#3" in body
