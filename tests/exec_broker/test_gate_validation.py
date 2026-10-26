"""hc#95 exec-broker gate + project_ref re-validation (contract §4)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.gate_validation import (  # noqa: E402
    BrokerValidationError,
    check_project_ref,
    require_gate,
)
from exec_broker.protocol import ErrorCode  # noqa: E402


class FakeCursor:
    """Records every .execute() call; .fetchone() returns the row it was built with."""

    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


def test_check_project_ref_allows_qa_ref_only():
    check_project_ref("oovskwogvefnznfowjky")  # no raise


def test_check_project_ref_rejects_the_two_real_refs():
    # Milestone (a) scope, enforced in code (orch-console gate ruling, bus
    # #70919): goumlyne/ceayj are REAL, CURRENT irsyad refs (not typos or
    # unknown strings) and must still be rejected until step (c) lands.
    for ref in ("goumlynecruxrlmzlntp", "ceayjeamtmcyzzvqflus"):
        with pytest.raises(BrokerValidationError) as exc:
            check_project_ref(ref)
        assert exc.value.code == ErrorCode.BAD_PROJECT_REF


def test_check_project_ref_rejects_unknown_ref():
    with pytest.raises(BrokerValidationError) as exc:
        check_project_ref("some-other-ref")
    assert exc.value.code == ErrorCode.BAD_PROJECT_REF


# --- bounded override (op#34322/#76985/#76989) -----------------------------

_NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
_GOUMLYNE = "goumlynecruxrlmzlntp"


def test_check_project_ref_override_allows_within_expiry():
    check_project_ref(
        _GOUMLYNE,
        override_ref=_GOUMLYNE,
        override_expires_at=_NOW + timedelta(hours=1),
        now=_NOW,
    )  # no raise


def test_check_project_ref_override_denies_after_expiry():
    with pytest.raises(BrokerValidationError) as exc:
        check_project_ref(
            _GOUMLYNE,
            override_ref=_GOUMLYNE,
            override_expires_at=_NOW - timedelta(seconds=1),
            now=_NOW,
        )
    assert exc.value.code == ErrorCode.BAD_PROJECT_REF


def test_check_project_ref_override_denies_a_different_ref():
    # An active override for goumlyne must not accidentally widen ceayj too.
    with pytest.raises(BrokerValidationError) as exc:
        check_project_ref(
            "ceayjeamtmcyzzvqflus",
            override_ref=_GOUMLYNE,
            override_expires_at=_NOW + timedelta(hours=1),
            now=_NOW,
        )
    assert exc.value.code == ErrorCode.BAD_PROJECT_REF


def test_check_project_ref_override_inert_when_absent():
    # Exactly the pre-override call shape — every existing caller/test above.
    with pytest.raises(BrokerValidationError):
        check_project_ref(_GOUMLYNE)


def test_check_project_ref_override_inert_when_only_ref_given_no_expiry():
    with pytest.raises(BrokerValidationError):
        check_project_ref(_GOUMLYNE, override_ref=_GOUMLYNE, now=_NOW)


def test_check_project_ref_qa_still_allowed_regardless_of_unrelated_override():
    check_project_ref(
        "oovskwogvefnznfowjky",
        override_ref=_GOUMLYNE,
        override_expires_at=_NOW - timedelta(hours=1),  # already expired
        now=_NOW,
    )  # no raise -- QA was never dependent on the override


def test_require_gate_noop_for_dml():
    cur = FakeCursor()
    require_gate("select 1", None, "oovskwogvefnznfowjky", cur=cur)  # no raise
    assert cur.calls == []  # cur never touched for DML


def test_require_gate_required_for_ddl_without_gate():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("alter table t add column x int", None, "goumlynecruxrlmzlntp", cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


def test_require_gate_invalid_when_gate_id_not_digits():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "abc", "goumlynecruxrlmzlntp", cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_invalid_when_gate_not_found():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "999999", "goumlynecruxrlmzlntp", cur=FakeCursor(None))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_invalid_when_wrong_from_agent():
    row = {"from_agent": "cc-hc95-broker", "message_type": "decision", "subject": "goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_invalid_when_wrong_message_type():
    row = {"from_agent": "orch-console", "message_type": "update", "subject": "goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError):
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))


def test_require_gate_invalid_when_not_past():
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "goumlyne", "body": "", "is_past": False}
    with pytest.raises(BrokerValidationError):
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))


def test_require_gate_invalid_when_wrong_silo_mentioned():
    # cross-silo trap: a real, valid-shaped gate for a DIFFERENT silo must not authorize this one
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to ceayj", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_refuses_even_a_fully_valid_shaped_gate(monkeypatch):
    # FAIL-CLOSED (bus #70929, corrects the earlier #70876 ruling): a gate
    # that is shape-valid on every axis (right agent/type/past/silo) must
    # STILL be refused when migration/pr can't be independently verified --
    # the broker must never allow DDL by default just because no mismatch
    # was found. This is the exact regression the original (fail-open) fix
    # introduced: a gate approving one migration would otherwise authorize
    # ANY DDL to the same silo.
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_refuses_even_a_fully_valid_shaped_gate_via_alias_qa():
    row = {"from_agent": "cai", "message_type": "agreed", "subject": "go for qa", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "1", "oovskwogvefnznfowjky", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_full_match_still_works_once_migration_pr_supplied():
    # Forward-compat proof for rollout step (c): once the wire contract
    # carries migration/pr and a caller passes them, the full independent
    # match is used INSTEAD of the unconditional refusal, and a genuinely
    # matching gate passes.
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True}
    require_gate(
        "drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row), migration="435",
    )  # no raise


def test_require_gate_full_match_rejects_mismatched_migration_once_supplied():
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            "drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row), migration="999",
        )
    assert exc.value.code == ErrorCode.GATE_INVALID
