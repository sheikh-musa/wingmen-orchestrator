"""hc#95 exec-broker gate + project_ref re-validation (contract §4)."""
from __future__ import annotations

import hashlib
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


def test_hazardous_with_matching_migration_pr_but_no_cited_sha12_is_a_gate_mismatch():
    # This test USED to pin the interim fail-closed behavior (hazardous SQL
    # refused even with a matching migration/pr, because no byte binding
    # existed). Part A 2/2 is exactly the change it predicted would have to
    # happen deliberately: the byte binding now exists, so a matching
    # migration/pr with NO cited wrapped sha12 is refused as GATE_MISMATCH
    # rather than GATE_INVALID -- the gate does not bind to these exact
    # bytes. The ALLOW path (matching migration/pr AND a correct citation)
    # is pinned by test_hazardous_passes_when_gate_cites_the_executed_bytes_sha12
    # in the Part A 2/2 section at the bottom of this file.
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            'SELECT "x--y"; DROP TABLE t', "56485", "goumlynecruxrlmzlntp",
            cur=FakeCursor(row), migration="435",
        )
    assert exc.value.code == ErrorCode.GATE_MISMATCH


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


# --- Part A 2/2 (orch-console ruling bus #79477): the byte binding for
# LEXICALLY HAZARDOUS SQL.
#
# The rule: the gater cites sha256(wrap(file))[:12] in the gate row; the
# broker refuses unless sha256(guarded_sql)[:12] equals that cited sha12.
# The broker never calls wrap() and never sees the migration file -- in
# console's words, "the broker proves the executed bytes equal what the
# gater hashed; the file identity is attested by the gater (via
# --show-gate-sha at the cited commit), not verified by the broker".
#
# These are the four pin tests named in #79477, plus the fail-closed edges
# the ruling's "unchanged" list implies (fragment/concatenation, ambiguous
# citation, and the non-hazardous branch not becoming reachable this way).
#
# NOTE ON THE SILO REF THESE USE: a synthetic ref, never a client one. The
# silo-mention check is plain whole-word matching against whatever
# project_ref the caller passes (the alias table only ADDS needles for real
# aliases), so nothing on this path is specific to a client silo -- and
# "code only, no client data" applies to test fixtures too.

_HAZ = 'SELECT "x--y"; DROP TABLE t'
_SYNTH_REF = "syntheticbrokerqfakesilo"


def _sha12(sql: str) -> str:
    """sha256 of the EXACT executed bytes, first 12 hex chars -- the same
    convention apply_migration.py already uses for gate citations
    (hashlib.sha256(...).hexdigest()[:12]), applied to the wrap()ed payload
    rather than to the raw file (the whole point of ruling (B))."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]


def _bytebound_row(
    cited: str | None = "__EXECUTED__",
    *,
    migration: str = "435",
    extra: str = "",
) -> dict:
    """A gate row shaped the way --show-gate-sha is meant to produce it: the
    commit, BOTH sha12s, with the wrapped one LABELLED (the raw-file one is
    recorded for cross-checking against apply_migration.py's separate,
    unchanged convention, and must never be what authorizes the call)."""
    body = "commit 5053826e; raw-sha12: 0123456789ab"
    if cited is not None:
        body += f"\nwrapped-sha12: {_sha12(_HAZ) if cited == '__EXECUTED__' else cited}"
    body += extra
    return {
        "from_agent": "orch-console",
        "message_type": "decision",
        "subject": f"apply migration {migration} to {_SYNTH_REF}",
        "body": body,
        "is_past": True,
    }


# (1) gated + byte-identical hazardous migration PASSES -- the ALLOW path the
# ruling exists to create. Without this, every real hazardous migration would
# still be refused and the ruling's intent silently defeated (the exact
# dead-on-arrival risk bus #79471 flagged).
def test_hazardous_passes_when_gate_cites_the_executed_bytes_sha12():
    require_gate(
        _HAZ, "56485", _SYNTH_REF,
        cur=FakeCursor(_bytebound_row()), migration="435",
    )  # no raise


# (2) one-byte-different hazardous SQL is REFUSED -- the exact failure the
# check exists to catch. A single trailing space changes sha256 but leaves
# every TEXT-matched axis (migration 435, silo, shape) identical.
def test_hazardous_refused_when_executed_bytes_differ_by_one_byte():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            _HAZ + " ", "56485", _SYNTH_REF,
            cur=FakeCursor(_bytebound_row()), migration="435",
        )
    assert exc.value.code == ErrorCode.GATE_MISMATCH


# (3) name-match but different file-sha REFUSED: the gate names the right
# migration and the right silo, but cites the sha12 of different bytes. This
# is the "gate approving one migration must not authorize another" property,
# now enforced on BYTES rather than on a number appearing in prose.
def test_hazardous_refused_when_gate_cites_a_different_sha12():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            _HAZ, "56485", _SYNTH_REF,
            cur=FakeCursor(_bytebound_row("deadbeefcafe")), migration="435",
        )
    assert exc.value.code == ErrorCode.GATE_MISMATCH


# (4) ungated hazardous REFUSED (GATE_REQUIRED, before any gate lookup) --
# listed in the ruling's unchanged set; pinned here too so this section
# carries all four named tests even though the pre-existing
# hazardous-without-gate tests above already cover the same path.
def test_hazardous_without_a_gate_is_refused_by_the_byte_binding_path_too():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_HAZ, None, _SYNTH_REF, cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


# A gate that names the right migration and silo but cites NO wrapped sha12
# (e.g. the old-shaped gate row, raw-file sha only) must be REFUSED: absence
# of a citation is not "nothing to compare, allow by default".
def test_hazardous_refused_when_gate_cites_no_wrapped_sha12():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            _HAZ, "56485", _SYNTH_REF,
            cur=FakeCursor(_bytebound_row(None)), migration="435",
        )
    assert exc.value.code == ErrorCode.GATE_MISMATCH


# Ambiguous citation (two DIFFERENT labelled wrapped sha12s) is refused
# rather than resolved by picking one -- an append/edit that leaves stale
# text behind must fail closed, not silently authorize whichever value the
# parser happened to see first.
def test_hazardous_refused_when_gate_cites_two_conflicting_wrapped_sha12s():
    row = _bytebound_row(extra="\nwrapped-sha12: deadbeefcafe")
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_HAZ, "56485", _SYNTH_REF, cur=FakeCursor(row), migration="435")
    assert exc.value.code == ErrorCode.GATE_MISMATCH


# An UNLABELLED 12-hex token is not a citation: the raw-file sha12 in the
# same row must not be mistaken for one (that would let the OLD convention
# authorize a wrapped call, i.e. re-create the #79471 byte-shape mismatch as
# a silent always-match).
def test_hazardous_refused_when_only_an_unlabelled_hex_token_is_present():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            _HAZ, "56485", _SYNTH_REF,
            # 0123456789ab is the row's raw-sha12, and here it is the whole
            # body -- no `wrapped-sha12:` label anywhere.
            cur=FakeCursor({"from_agent": "orch-console", "message_type": "decision",
                            "subject": f"apply migration 435 to {_SYNTH_REF}",
                            "body": "0123456789ab", "is_past": True}),
            migration="435",
        )
    assert exc.value.code == ErrorCode.GATE_MISMATCH


# Byte binding is ADDITIONAL, never a substitute for specificity (this
# module's deliberately fail-closed reading of the ruling): a gate whose
# citation is correct but which does not mention the migration/PR the caller
# claims is still GATE_INVALID -- exactly as the same gate would be for
# non-hazardous DDL.
def test_hazardous_still_requires_migration_or_pr_match_alongside_the_byte_binding():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            _HAZ, "56485", _SYNTH_REF,
            cur=FakeCursor(_bytebound_row(migration="435")), migration="999",
        )
    assert exc.value.code == ErrorCode.GATE_INVALID


# ...and with NEITHER migration nor pr on the wire (an old 6-key caller),
# a hazardous statement is still refused -- the byte binding does not open a
# second, un-upgraded route into the hazardous path.
def test_hazardous_refused_for_an_old_caller_that_supplies_no_migration_or_pr():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_HAZ, "56485", _SYNTH_REF, cur=FakeCursor(_bytebound_row()))
    assert exc.value.code == ErrorCode.GATE_INVALID


# The NON-hazardous DDL branch must NOT become reachable via a citation: a
# gate citing the right sha12 for a plain DDL statement, with no migration/pr
# on the wire, is still the milestone-(a) unconditional refusal. This pins
# the byte binding to the hazardous path only, as ruled ("for
# broker-executed lexically-hazardous SQL only").
def test_non_hazardous_ddl_cannot_pass_on_a_cited_sha12_alone():
    sql = "drop table t"
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": f"apply to {_SYNTH_REF}", "is_past": True,
           "body": f"wrapped-sha12: {_sha12(sql)}"}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, "56485", _SYNTH_REF, cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID
