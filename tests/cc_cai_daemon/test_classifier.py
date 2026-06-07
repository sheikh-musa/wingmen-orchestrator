"""Classifier tests — CAI-RESP-185 Q4 narrow silent-lane.

Per CAI-RESP-185:
  - silent-lane authority is strictly mark_read_fyi + ack_fyi
  - INV-6 default HOLD: unknown shapes / low confidence MUST escalate
  - Phase 1 is RULES-ONLY (no LLM)

Layer order (first-match wins):
  Layer 1: INV-1 reaches-operator keyword triggers (highest priority)
  Layer 2: P0/P1 priority OR challenge/blocker/counter message_type
  Layer 3: recognized routine FYI patterns (silent-lane targets)
  Layer 4: INV-6 default HOLD (escalate with low confidence)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cc_cai_daemon.classifier import (  # noqa: E402
    Classification,
    REACHES_OPERATOR_CATEGORIES,
    classify,
)


# ---------- helpers ----------

def _msg(
    *,
    subject: str = "",
    body: str = "",
    priority: str = "P3",
    message_type: str = "update",
    requires_response: bool = False,
    **extra,
) -> dict:
    base = {
        "subject": subject,
        "body": body,
        "priority": priority,
        "message_type": message_type,
        "requires_response": requires_response,
    }
    base.update(extra)
    return base


# ---------- frozenset immutability ----------

def test_reaches_operator_categories_is_frozenset():
    assert isinstance(REACHES_OPERATOR_CATEGORIES, frozenset)
    with pytest.raises(AttributeError):
        REACHES_OPERATOR_CATEGORIES.add("foo")  # type: ignore[attr-defined]


def test_reaches_operator_categories_has_all_eight():
    expected = {
        "halal_riba",
        "zakat",
        "client_data_amanah",
        "client_facing_commitment",
        "ip_legal_contract",
        "money_above_threshold",
        "irreversible_destructive",
        "novel_low_confidence",
    }
    assert REACHES_OPERATOR_CATEGORIES == expected


# ---------- Layer 1: INV-1 reaches-operator triggers ----------

def test_riba_keyword_escalates():
    m = _msg(subject="Should we ship BNPL flow", body="")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "halal_riba"
    assert r.confidence == 1.0


def test_zakat_keyword_escalates():
    m = _msg(subject="zakat reporting question", body="")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "zakat"
    assert r.confidence == 1.0


def test_client_data_pii_escalates():
    m = _msg(subject="proposal", body="will store PII fields in plaintext")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "client_data_amanah"
    assert r.confidence == 1.0


def test_rls_substrate_vocab_does_not_trigger_amanah(
    # Per CAI-RESP-188: 'rls' (Row Level Security) is substrate-engineering
    # vocab, not a client_data_amanah signal. Tightening this pattern is the
    # only blind classifier change ratified before the 30-day calibration.
):
    m = _msg(
        subject="ARCH-035 RLS policy refactor",
        body="propose RLS on agent_messages table; subset of policies refactored",
        priority="P3",
        message_type="update",
    )
    r = classify(m)
    if r.label == "escalate":
        assert r.escalation_category != "client_data_amanah", (
            f"rls (Row Level Security) substrate vocab must not match "
            f"client_data_amanah — got reason: {r.reason}"
        )


def test_client_facing_commitment_escalates():
    m = _msg(subject="draft", body="propose 99.9% uptime SLA to client")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "client_facing_commitment"
    assert r.confidence == 1.0


def test_ip_legal_keyword_escalates():
    m = _msg(subject="logo concern", body="possible trademark conflict")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "ip_legal_contract"
    assert r.confidence == 1.0


def test_money_above_threshold_escalates():
    m = _msg(subject="hosting", body="approve spend $200/mo cluster")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "money_above_threshold"
    assert r.confidence == 1.0


def test_irreversible_escalates():
    m = _msg(subject="db cleanup", body="run DROP TABLE legacy_users")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "irreversible_destructive"
    assert r.confidence == 1.0


def test_novel_escalates_with_low_confidence():
    # unknown message_type, no triggers — falls through to Layer 4
    m = _msg(message_type="weirdtype", subject="???", body="???")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "novel_low_confidence"
    assert r.confidence < 0.5


# ---------- Layer 2: high priority / blocker types ----------

def test_p0_priority_escalates():
    m = _msg(priority="P0", subject="prod down", body="")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "novel_low_confidence"
    assert r.confidence == 0.95


def test_p1_priority_escalates():
    m = _msg(priority="P1", message_type="update", subject="urgent", body="")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "novel_low_confidence"
    assert r.confidence == 0.95


def test_challenge_type_escalates():
    m = _msg(message_type="challenge", priority="P3", subject="disagree", body="")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "novel_low_confidence"


def test_blocker_type_escalates():
    m = _msg(message_type="blocker", priority="P3", subject="stuck", body="")
    r = classify(m)
    assert r.label == "escalate"


def test_counter_type_escalates():
    m = _msg(message_type="counter", priority="P3", subject="counter-proposal", body="")
    r = classify(m)
    assert r.label == "escalate"


# ---------- Layer 3: routine FYI patterns ----------

def test_tripwire_report_ack_fyi():
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P3",
        subject="tripwire +24h clean",
        body="no anomalies",
    )
    r = classify(m)
    assert r.label == "ack_fyi"
    assert r.escalation_category is None
    assert r.confidence == 0.9


def test_calibration_summary_ack_fyi():
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P3",
        subject="phase-1 calibration summary",
        body="",
    )
    r = classify(m)
    assert r.label == "ack_fyi"
    assert r.confidence == 0.85


def test_findings_ack_fyi():
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P2",
        subject="FINDINGS from audit window",
        body="",
    )
    r = classify(m)
    assert r.label == "ack_fyi"


def test_accepted_by_timeout_mark_read():
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P3",
        subject="proposal-x accepted_by_timeout",
        body="",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"
    assert r.escalation_category is None
    assert r.confidence == 0.95


def test_digest_mark_read():
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P4",
        subject="daily digest",
        body="",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"
    assert r.confidence == 0.9


def test_status_md_mark_read():
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P3",
        subject="STATUS.md updated",
        body="",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"


def test_generic_update_fyi_mark_read():
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P3",
        subject="some progress note",
        body="just FYI",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"
    assert r.confidence >= 0.7


def test_agreed_fyi_mark_read():
    m = _msg(
        message_type="agreed",
        requires_response=False,
        priority="P3",
        subject="agreement",
        body="",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"
    assert r.confidence == 0.9


def test_digest_type_mark_read():
    m = _msg(
        message_type="digest",
        requires_response=False,
        priority="P3",
        subject="weekly digest",
        body="",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"
    assert r.confidence == 0.95


def test_decision_accepted_by_timeout_mark_read():
    m = _msg(
        message_type="decision",
        requires_response=False,
        priority="P3",
        subject="DEC-007 accepted_by_timeout",
        body="",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"
    assert r.confidence == 0.95


def test_decision_other_escalates():
    m = _msg(
        message_type="decision",
        requires_response=False,
        priority="P3",
        subject="DEC-008 ratified",
        body="",
    )
    r = classify(m)
    assert r.label == "escalate"


# ---------- Layer 4: INV-6 default HOLD ----------

def test_unknown_message_type_escalates():
    m = _msg(
        message_type="rumor",
        requires_response=False,
        priority="P3",
        subject="hmm",
        body="",
    )
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "novel_low_confidence"
    assert r.confidence == 0.4


def test_p2_question_requires_response_escalates():
    m = _msg(
        message_type="question",
        requires_response=True,
        priority="P2",
        subject="want input on X",
        body="",
    )
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "novel_low_confidence"


# ---------- Layer order precedence ----------

def test_riba_keyword_wins_over_p3_update_fyi():
    """Layer 1 (keyword) dominates Layer 3 (routine FYI)."""
    m = _msg(
        message_type="update",
        requires_response=False,
        priority="P3",
        subject="weekly digest",
        body="reminder: BNPL flow under review",
    )
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "halal_riba"


def test_irreversible_wins_over_digest_type():
    """Layer 1 dominates Layer 3 even for message_type='digest'."""
    m = _msg(
        message_type="digest",
        requires_response=False,
        priority="P3",
        subject="weekly digest",
        body="we will DROP TABLE old_sessions tonight",
    )
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category == "irreversible_destructive"


def test_challenge_type_wins_over_no_keyword():
    """Layer 2 dominates Layer 4."""
    m = _msg(
        message_type="challenge",
        requires_response=False,
        priority="P3",
        subject="pushback",
        body="",
    )
    r = classify(m)
    assert r.label == "escalate"


# ---------- never raises ----------

def test_empty_dict_does_not_raise():
    r = classify({})
    assert r.label == "escalate"
    assert r.escalation_category == "novel_low_confidence"


def test_none_values_do_not_raise():
    r = classify({"subject": None, "body": None, "priority": None, "message_type": None})
    assert r.label == "escalate"


def test_classification_is_frozen_dataclass():
    r = classify({})
    with pytest.raises(Exception):  # FrozenInstanceError is dataclasses.FrozenInstanceError
        r.label = "mark_read_fyi"  # type: ignore[misc]


# ---------- escalation_category constraint ----------

def test_non_escalate_has_no_category():
    m = _msg(
        message_type="digest",
        requires_response=False,
        priority="P3",
        subject="weekly",
        body="all green",
    )
    r = classify(m)
    assert r.label == "mark_read_fyi"
    assert r.escalation_category is None


def test_escalate_category_is_from_enumerated_set():
    m = _msg(subject="BNPL question", body="")
    r = classify(m)
    assert r.label == "escalate"
    assert r.escalation_category in REACHES_OPERATOR_CATEGORIES
