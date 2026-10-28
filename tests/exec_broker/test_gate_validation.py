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
    ref_override_is_live,
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


# --- (c): reuse the RO-override's lexical hazard pre-screen on the base
# is_ddl_shaped path (cc-fleet-health bus #79161, after the PR #569
# identifier-$ review). "One test per construct" — see each test's own
# comment for whether the construct is a DEMONSTRATED is_ddl_shaped miss on
# this path. The dquote class and the backslash class BOTH are (the
# backslash one via PR #570 review comment 6459367412, 2026-10-28 --
# confirmed independently below before accepting the correction).
# identifier-$ is NOT a demonstrated miss here specifically, because
# is_ddl_shaped's own dollar-body DDL-keyword rescan already defends that
# class when a real DDL keyword is present inside the swallowed region --
# but ambiguous/hazardous input is refused regardless, same fail-closed
# doctrine as the RO-override screen.

def test_require_gate_refuses_lexically_hazardous_dquote_without_gate():
    # DEMONSTRATED pre-existing miss (whole-branch review, non-blocking item,
    # 2026-10-28): the vendored lexer has no double-quoted-identifier state
    # at all, so a bare "--" after a quote char opens a LINE COMMENT (not a
    # dollar body -- never re-scanned), permanently hiding "; DROP TABLE t"
    # from is_ddl_shaped. Confirmed is_ddl_shaped(sql) is False for this
    # exact string before this fix.
    with pytest.raises(BrokerValidationError) as exc:
        require_gate('SELECT "x--y"; DROP TABLE t', None, "goumlynecruxrlmzlntp", cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


def test_require_gate_refuses_lexically_hazardous_dquote_even_with_a_valid_gate():
    # Same milestone-(a) fail-closed doctrine as
    # test_require_gate_refuses_even_a_fully_valid_shaped_gate: a hazardous
    # statement with a shape-valid gate is STILL refused (GATE_INVALID), not
    # silently allowed once a gate_id is attached.
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate('SELECT "x--y"; DROP TABLE t', "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_refuses_lexically_hazardous_identifier_dollar_without_gate():
    # Defense-in-depth/consistency, NOT a demonstrated is_ddl_shaped miss for
    # this exact string (no DDL keyword appears anywhere, so is_ddl_shaped
    # was already False for an unrelated reason -- this test instead proves
    # the wiring: hazardous input is gated even with nothing DDL-shaped to
    # find, same "cannot safely classify -> refuse" posture as the RO path.
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("SELECT col$1 FROM t", None, "goumlynecruxrlmzlntp", cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


@pytest.mark.parametrize("sql", [
    # DEMONSTRATED pre-existing miss (PR #570 review, 2026-10-28): under
    # standard_conforming_strings=off (session-settable mid-statement, no
    # special privilege needed), PG reads '\'' as a ONE-CHAR string (the
    # backslash escapes the quote), so the string closes right there and
    # "; DROP TABLE t; --'" is REAL CODE to PG. The vendored lexer does NOT
    # know about backslash-escaping in plain '...' strings -- it sees the
    # two quotes at positions 2-3 as a DOUBLED-QUOTE escape ('') and keeps
    # scanning for the NEXT unescaped quote, which is the trailing one after
    # "--", swallowing "; DROP TABLE t; --" whole as still-inside-the-string.
    # is_ddl_shaped(sql) is False for both forms below, confirmed directly
    # before writing this test (never accept a correction unverified).
    "SET standard_conforming_strings = off; SELECT '\\''; DROP TABLE t; --'",
    "SELECT set_config('standard_conforming_strings','off',false); SELECT '\\''; DROP TABLE t; --'",
])
def test_require_gate_refuses_lexically_hazardous_backslash_demonstrated_miss_without_gate(sql):
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, None, "goumlynecruxrlmzlntp", cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


def test_require_gate_refuses_lexically_hazardous_backslash_harmless_case_too_without_gate():
    # This specific construct (a harmless E-string escape, no DDL keyword
    # anywhere) is not itself exploitable -- but the backslash CLASS as a
    # whole now has a confirmed real exploit (the SCS-off test above), so
    # refusing even the harmless case is the rule doing its job, not mere
    # defense-in-depth for its own sake.
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("SELECT E'\\x41' FROM t", None, "goumlynecruxrlmzlntp", cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED



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


def test_require_gate_hazardous_still_refused_even_with_matching_migration_pr():
    # The gap orch-console ruled on (bus #79465/#79468, 2026-10-28): wiring
    # migration/pr through must NOT also let LEXICALLY HAZARDOUS SQL pass on
    # a text-mention match alone -- console approved allowing hazardous SQL
    # through only WITH an additional byte-binding check (sha256 of the
    # executed bytes matching a sha12 the gate cites), which does not exist
    # yet (open feasibility question, bus #79471). Until that lands,
    # hazardous SQL stays refused regardless of how well migration/pr match
    # the gate text -- this is the fail-closed INTERIM behavior, pinned so a
    # future byte-binding PR has to deliberately change this test, not drift
    # into it by accident.
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            'SELECT "x--y"; DROP TABLE t', "56485", "goumlynecruxrlmzlntp",
            cur=FakeCursor(row), migration="435",
        )
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_full_match_rejects_mismatched_migration_once_supplied():
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            "drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row), migration="999",
        )
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_ref_override_is_live_requires_both_halves():
    # The ONE definition of "the bounded ref override is currently in force"
    # (shared by check_project_ref and read_only.check_read_only_override):
    # armed means BOTH halves present AND unexpired.
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert ref_override_is_live("goumlynecruxrlmzlntp", future) is True
    assert ref_override_is_live(None, future) is False
    assert ref_override_is_live("goumlynecruxrlmzlntp", None) is False


def test_ref_override_is_live_expiry_is_strict():
    ref = "goumlynecruxrlmzlntp"
    boundary = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert ref_override_is_live(ref, boundary, now=boundary - timedelta(seconds=1)) is True
    assert ref_override_is_live(ref, boundary, now=boundary) is False  # == expiry is expired
    assert ref_override_is_live(ref, boundary, now=boundary + timedelta(seconds=1)) is False
