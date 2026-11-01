"""hc#95 exec-broker gate + project_ref re-validation (contract §4)."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "lib"))

from exec_broker.gate_validation import (  # noqa: E402
    DEFAULT_GATE_MAX_AGE_SECONDS,
    DEFAULT_LIVE_GATED_REFS,
    MAX_GATE_MAX_AGE_SECONDS,
    MIN_GATE_MAX_AGE_SECONDS,
    BrokerValidationError,
    _text_mentions_ref,
    _validate_ref,
    check_project_ref,
    ref_override_is_live,
    require_gate,
)
from exec_broker.protocol import ErrorCode  # noqa: E402

# B3 cutover floor (console #89841 ruling e): require_gate refuses every gate
# on a non-QA ref when no floor is configured. Pre-existing tests exercise
# OTHER properties on non-QA refs, so this module's require_gate defaults a
# floor far in the past; the floor itself (unset / before / after) is tested
# explicitly against _raw_require_gate in the B3 section at the end.
_EPOCH_FLOOR = datetime(2000, 1, 1, tzinfo=timezone.utc)
_raw_require_gate = require_gate


def require_gate(*args, **kwargs):  # noqa: F811 -- deliberate test shim, see above
    kwargs.setdefault("gate_not_before", _EPOCH_FLOOR)
    return _raw_require_gate(*args, **kwargs)


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
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to goumlyne", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
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
    row = {"from_agent": "cc-hc95-broker", "message_type": "decision", "subject": "goumlyne", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_invalid_when_wrong_message_type():
    row = {"from_agent": "orch-console", "message_type": "update", "subject": "goumlyne", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
    with pytest.raises(BrokerValidationError):
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))


def test_require_gate_invalid_when_not_past():
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "goumlyne", "body": "", "is_past": False}
    with pytest.raises(BrokerValidationError):
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))


def test_require_gate_invalid_when_wrong_silo_mentioned():
    # cross-silo trap: a real, valid-shaped gate for a DIFFERENT silo must not authorize this one
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to ceayj", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
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
    row = {"from_agent": "orch-console", "message_type": "decision", "subject": "apply to goumlyne", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_refuses_even_a_fully_valid_shaped_gate_via_alias_qa():
    row = {"from_agent": "cai", "message_type": "agreed", "subject": "go for qa", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "1", "oovskwogvefnznfowjky", cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_require_gate_full_match_still_works_once_migration_pr_supplied():
    # Forward-compat proof for rollout step (c): once the wire contract
    # carries migration/pr and a caller passes them, the full independent
    # match is used INSTEAD of the unconditional refusal, and a genuinely
    # matching gate passes.
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
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
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(
            'SELECT "x--y"; DROP TABLE t', "56485", "goumlynecruxrlmzlntp",
            cur=FakeCursor(row), migration="435",
        )
    assert exc.value.code == ErrorCode.GATE_MISMATCH


def test_require_gate_full_match_rejects_mismatched_migration_once_supplied():
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True, "is_fresh": True, "after_cutover": True}
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
        "is_past": True, "is_fresh": True, "after_cutover": True,
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
                            "body": "0123456789ab", "is_past": True, "is_fresh": True, "after_cutover": True}),
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
           "subject": f"apply to {_SYNTH_REF}", "is_past": True, "is_fresh": True, "after_cutover": True,
           "body": f"wrapped-sha12: {_sha12(sql)}"}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, "56485", _SYNTH_REF, cur=FakeCursor(row))
    assert exc.value.code == ErrorCode.GATE_INVALID


# --- B1 (bus #89814 §1): every non-plain-read statement on a gated ref is
# gated -- DML included. Mirrors exec_prod._require_gate (console #81213).
# Synthetic silo ref throughout (see the note above _SYNTH_REF).

_DML_UPDATE = "UPDATE tabung_tins SET amount = 12.50 WHERE id = 42;"
_DO_DATA_FIX = "DO $$\nBEGIN\n  UPDATE tabung_tins SET amount = 12.50 WHERE id = 42;\nEND\n$$;"
_DO_PURE_DML = "DO $$ BEGIN INSERT INTO t (a) VALUES (1); DELETE FROM t WHERE a = 2; END $$;"


def _ref_row(**overrides) -> dict:
    row = {
        "from_agent": "orch-console",
        "message_type": "decision",
        "subject": "GO data correction op#36027",
        "body": f"GO: apply the op#36027 tin-amount correction on {_SYNTH_REF}. No DDL.",
        "is_past": True, "is_fresh": True, "after_cutover": True,
    }
    row.update(overrides)
    return row


def test_b1_dml_without_gate_is_gate_required():
    for sql in (_DML_UPDATE, _DO_DATA_FIX, _DO_PURE_DML, "insert into t values (1)",
                "delete from t", "with d as (delete from t returning *) select * from d"):
        cur = FakeCursor()
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(sql, None, _SYNTH_REF, cur=cur)
        assert exc.value.code == ErrorCode.GATE_REQUIRED, sql
        assert cur.calls == [], "GATE_REQUIRED must refuse before any bus lookup"


def test_b1_dml_without_gate_is_gate_required_on_qa_too():
    # The QA default ref is a gated ref like any other (fail-closed reading of
    # the spec; see the PR body "Deviations").
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, None, "oovskwogvefnznfowjky", cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


def test_b1_dml_with_valid_ref_anchored_gate_passes_and_reports_a_gate_was_used():
    cur = FakeCursor(_ref_row())
    assert require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=cur, ref="op#36027") is True
    assert len(cur.calls) == 1 and cur.calls[0][1] == (86400, _EPOCH_FLOOR, 83934)


def test_b1_dml_with_gate_not_mentioning_the_silo_is_gate_invalid():
    row = _ref_row(subject="GO op#36027", body="GO op#36027 data fix on some other silo only.")
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(row), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID
    assert "silo" in exc.value.message


def test_b1_dml_with_gate_but_no_anchor_on_the_wire_is_gate_invalid():
    # A gate that merely names the silo must not authorize arbitrary DML
    # (same cross-authorization class as #70929) -- exec_prod refuses this too.
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row()))
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_b1_dml_with_matching_pr_or_migration_anchor_passes():
    row = _ref_row(body=f"GO PR#1040 migration 479 on {_SYNTH_REF}")
    assert require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(row), pr="1040") is True
    assert require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(row), migration="479") is True
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(row), pr="999")
    assert exc.value.code == ErrorCode.GATE_INVALID


@pytest.mark.parametrize("sql", [
    "select 1",
    "SELECT count(*) FROM t WHERE a = 'x'",
    "with x as (select id from t) select count(*) from x",
    "table t",
    "values (1), (2)",
])
def test_b1_plain_read_on_gated_ref_needs_no_gate_and_never_touches_cur(sql):
    cur = FakeCursor()
    assert require_gate(sql, None, _SYNTH_REF, cur=cur) is False
    assert cur.calls == []


def test_b1_plain_read_with_a_gate_id_attached_still_reports_no_gate_used():
    # A gate cited on a plain read is not validated -- and so (B3) not consumed.
    cur = FakeCursor(_ref_row())
    assert require_gate("select 1", "83934", _SYNTH_REF, cur=cur) is False
    assert cur.calls == []


@pytest.mark.parametrize("sql", [
    "select * from t for update",
    "select nextval('s')",
    "select set_config('a.b', 'c', true)",
    "select 1 into tmp_t",
])
def test_b1_read_shaped_writes_are_not_plain_reads_and_need_a_gate(sql):
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(sql, None, _SYNTH_REF, cur=FakeCursor())
    assert exc.value.code == ErrorCode.GATE_REQUIRED


# --- --ref anchor (bus #89814 §3), mirroring exec_prod.test.py (a)-(g) ------

def test_ref_a_data_fix_with_matching_ref_passes():
    """(a) DML / DO-block data correction + ref op#36027, gate names op#36027 + silo."""
    for sql in (_DO_DATA_FIX, _DML_UPDATE, _DO_PURE_DML):
        cur = FakeCursor(_ref_row())
        assert require_gate(sql, "83934", _SYNTH_REF, cur=cur, ref="op#36027") is True
        assert cur.calls[0][1] == (86400, _EPOCH_FLOOR, 83934)


def test_ref_b_mismatched_ref_refused_digit_boundary():
    """(b) gate says op#36027; a prefix, a different prefix, or a suffix-cut ref
    must NOT match."""
    for bad in ("op#3602", "hc#36027", "op#6027"):
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(_DO_DATA_FIX, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row()), ref=bad)
        assert exc.value.code == ErrorCode.GATE_INVALID
        assert "does not mention ref" in exc.value.message
    assert _text_mentions_ref("see op#36027.", "op#36027")
    assert _text_mentions_ref("(OP#36027)", "op#36027")
    assert not _text_mentions_ref("op#36027", "op#3602")
    assert not _text_mentions_ref("faq#210 is unrelated", "q#210")
    assert not _text_mentions_ref("hcp#5", "cp#5")
    assert not _text_mentions_ref("op#360270", "op#36027")


def test_ref_op51_and_op510_never_cross_match():
    assert not _text_mentions_ref("GO op#510 on qa", "op#51")
    assert not _text_mentions_ref("GO op#51 on qa", "op#510")
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "1", _SYNTH_REF,
                     cur=FakeCursor(_ref_row(subject="GO op#510", body=f"op#510 on {_SYNTH_REF}")),
                     ref="op#51")
    assert exc.value.code == ErrorCode.GATE_INVALID
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "1", _SYNTH_REF,
                     cur=FakeCursor(_ref_row(subject="GO op#51", body=f"op#51 on {_SYNTH_REF}")),
                     ref="op#510")
    assert exc.value.code == ErrorCode.GATE_INVALID
    # control: the exact token passes
    assert require_gate(_DML_UPDATE, "1", _SYNTH_REF,
                        cur=FakeCursor(_ref_row(subject="GO op#51", body=f"op#51 on {_SYNTH_REF}")),
                        ref="op#51") is True


_DDL_INNER = "ALTER TABLE t ADD COLUMN x int;"
_GRANT_INNER = "GRANT SELECT ON t TO authenticated;"
_DO_DDL_LITERAL = "DO $$ BEGIN CREATE TABLE z (a int); END $$;"
_DO_DYNAMIC_DDL = "DO $$ BEGIN EXECUTE 'DROP TABLE z'; END $$;"


def test_ref_c_ddl_with_only_ref_refused_before_the_bus_lookup():
    """(c) DDL -- top-level, GRANT, inside a DO body, via dynamic EXECUTE -- with
    ref as the only anchor is refused even when the gate names the ref + silo."""
    row = _ref_row(body=f"GO op#36027 on {_SYNTH_REF}, mig999, PR#1040")
    for sql in (_DDL_INNER, _GRANT_INNER, _DO_DDL_LITERAL, _DO_DYNAMIC_DDL):
        cur = FakeCursor(row)
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(sql, "83934", _SYNTH_REF, cur=cur, ref="op#36027")
        assert exc.value.code == ErrorCode.GATE_INVALID, sql
        assert "only a gate anchor" in exc.value.message
        assert cur.calls == [], "DDL + ref-only must refuse before the bus lookup"
    # ...and with no gate at all it is still the ref refusal, not GATE_REQUIRED
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DDL_INNER, None, _SYNTH_REF, cur=FakeCursor(), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_ref_only_on_lexically_hazardous_sql_refused_before_the_bus_lookup():
    for sql in (_HAZ, "SELECT col$1 FROM t", "UPDATE t SET a = E'\\x41'"):
        cur = FakeCursor(_ref_row(body=f"op#36027 on {_SYNTH_REF} wrapped-sha12: {_sha12(sql)}"))
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(sql, "83934", _SYNTH_REF, cur=cur, ref="op#36027")
        assert exc.value.code == ErrorCode.GATE_INVALID, sql
        assert cur.calls == []


def test_ref_c2_ddl_with_ref_plus_pr_still_needs_pr_match():
    """ref never anchors DDL: with pr also passed, the PR must still match."""
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DDL_INNER, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row()),
                     ref="op#36027", pr="1040")
    assert exc.value.code == ErrorCode.GATE_INVALID
    assert "does not mention migration" in exc.value.message
    row = _ref_row(body=f"GO op#36027 PR#1040 on {_SYNTH_REF}")
    assert require_gate(_DDL_INNER, "83934", _SYNTH_REF, cur=FakeCursor(row),
                        ref="op#36027", pr="1040") is True
    # ...and the ref, once passed, must ALSO match even though PR anchors DDL
    row = _ref_row(subject="GO", body=f"GO PR#1040 on {_SYNTH_REF}")
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DDL_INNER, "83934", _SYNTH_REF, cur=FakeCursor(row),
                     ref="op#36027", pr="1040")
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_ref_d_gate_naming_ref_but_not_silo_refused():
    """(d) gate names op#36027 but not the target silo."""
    row = _ref_row(subject="GO op#36027", body="GO op#36027 data fix on the QA silo only.")
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DO_DATA_FIX, "83934", _SYNTH_REF, cur=FakeCursor(row), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID
    assert "does not mention the target silo" in exc.value.message


_BAD_REF_SHAPES = (
    "op#", "op#12a", " op#12", "OP#12", "op#12 ",               # bus #89814 §3 list
    "36027", "#36027", "op#36027\n", "op#36027;", "xx#1", "op 36027",
    "op#٣", "op#1 OR 1=1", "Op#1", "op##1", "",
)


@pytest.mark.parametrize("bad", _BAD_REF_SHAPES)
def test_ref_e_bad_ref_shape_refused_before_the_bus(bad):
    """(e) anything not exactly <op|hc|q|cp>#<ascii digits> is refused -- before
    the bus, for DML, for a plain read, and even when pr is also present."""
    for sql, kw in ((_DO_DATA_FIX, {}), ("select 1", {}), (_DML_UPDATE, {"pr": "1040"})):
        cur = FakeCursor(_ref_row())
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(sql, "83934", _SYNTH_REF, cur=cur, ref=bad, **kw)
        assert exc.value.code == ErrorCode.GATE_INVALID
        assert "ref must look like" in exc.value.message
        assert cur.calls == []


def test_ref_e_good_shapes_accepted():
    for good in ("op#36027", "hc#944", "q#210", "cp#7"):
        assert _validate_ref(good) == good


def test_ref_f_gate_type_sender_and_time_checks_still_apply():
    """(f) ref loosens nothing else: sender, message_type, is_past, gate-id shape,
    not-found, and a missing gate are all still enforced."""
    for row in (_ref_row(from_agent="cc-irsyad-coord"), _ref_row(message_type="info"),
                _ref_row(is_past=False), None):
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(_DO_DATA_FIX, "83934", _SYNTH_REF, cur=FakeCursor(row), ref="op#36027")
        assert exc.value.code == ErrorCode.GATE_INVALID
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DO_DATA_FIX, None, _SYNTH_REF, cur=FakeCursor(), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_REQUIRED
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DO_DATA_FIX, "op#36027", _SYNTH_REF, cur=FakeCursor(), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_ref_f2_existing_migration_and_pr_anchors_unchanged():
    """(f) migration/pr behave exactly as before when ref is absent, and a gate
    that names only a ref cannot satisfy a pr/migration-only exec."""
    row = _ref_row(subject="apply PR#1040", body=f"PR#1040 on {_SYNTH_REF}")
    assert require_gate(_DML_UPDATE, "56485", _SYNTH_REF, cur=FakeCursor(row), pr="1040") is True
    with pytest.raises(BrokerValidationError):
        require_gate(_DML_UPDATE, "56485", _SYNTH_REF, cur=FakeCursor(row), pr="999")
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DO_DATA_FIX, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row()), pr="1040")
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_ref_g_ref_plus_migration_refused_before_the_bus():
    """(g) ref + migration together is an ambiguous anchor -- refused outright,
    for DML and DDL alike, before the bus lookup."""
    row = _ref_row(body=f"GO op#36027 mig479 on {_SYNTH_REF}")
    for sql in (_DO_DATA_FIX, _DDL_INNER, "select 1"):
        cur = FakeCursor(row)
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(sql, "83934", _SYNTH_REF, cur=cur, ref="op#36027", migration="479")
        assert exc.value.code == ErrorCode.GATE_INVALID
        assert "together" in exc.value.message
        assert cur.calls == []


def test_ref_g_ref_anchored_statement_may_never_stamp_schema_migrations():
    """(g) no schema_migrations stamp under a ref-only gate."""
    stamp = (
        "UPDATE t SET a = 1;\n"
        "INSERT INTO supabase_migrations.schema_migrations (version, name) "
        "VALUES ('479', 'x') ON CONFLICT (version) DO NOTHING;"
    )
    cur = FakeCursor(_ref_row())
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(stamp, "83934", _SYNTH_REF, cur=cur, ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID
    assert "schema_migrations" in exc.value.message
    assert cur.calls == []


def test_ref_plus_pr_on_hazardous_sql_still_needs_the_byte_binding():
    # ref never weakens the hazardous path: ref must match, migration/pr must
    # match, AND the wrapped-sha12 must bind the exact bytes.
    body = f"op#36027 PR#1040 on {_SYNTH_REF}\nwrapped-sha12: {_sha12(_HAZ)}"
    assert require_gate(_HAZ, "1", _SYNTH_REF, cur=FakeCursor(_ref_row(body=body)),
                        ref="op#36027", pr="1040") is True
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_HAZ + " ", "1", _SYNTH_REF, cur=FakeCursor(_ref_row(body=body)),
                     ref="op#36027", pr="1040")
    assert exc.value.code == ErrorCode.GATE_MISMATCH


# --- B1 config: check_project_ref honours the boot-loaded standing set -----

def test_check_project_ref_default_allowlist_is_qa_only_when_unconfigured():
    assert DEFAULT_LIVE_GATED_REFS == frozenset({"oovskwogvefnznfowjky"})
    check_project_ref("oovskwogvefnznfowjky", allowed_refs=None)
    with pytest.raises(BrokerValidationError):
        check_project_ref(_GOUMLYNE, allowed_refs=None)


def test_check_project_ref_widened_standing_set_allows_its_refs_only():
    widened = frozenset({"oovskwogvefnznfowjky", _GOUMLYNE})
    check_project_ref(_GOUMLYNE, allowed_refs=widened)
    with pytest.raises(BrokerValidationError) as exc:
        check_project_ref("ceayjeamtmcyzzvqflus", allowed_refs=widened)
    assert exc.value.code == ErrorCode.BAD_PROJECT_REF


def test_check_project_ref_narrowed_standing_set_drops_qa():
    with pytest.raises(BrokerValidationError) as exc:
        check_project_ref("oovskwogvefnznfowjky", allowed_refs=frozenset({_GOUMLYNE}))
    assert exc.value.code == ErrorCode.BAD_PROJECT_REF


def test_check_project_ref_empty_standing_set_denies_everything():
    with pytest.raises(BrokerValidationError):
        check_project_ref("oovskwogvefnznfowjky", allowed_refs=frozenset())


# --- B3 (bus #89814 §4.1): gate max age + canonical gate_id ----------------

def test_b3_stale_gate_is_gate_invalid():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row(is_fresh=False)), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID
    assert "max gate age" in exc.value.message


def test_b3_row_without_a_freshness_verdict_fails_closed():
    row = _ref_row()
    del row["is_fresh"]
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(row), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_b3_stale_gate_refused_on_ddl_and_hazardous_paths_too():
    row = {"from_agent": "orch-console", "message_type": "decision",
           "subject": "apply migration 435 to goumlyne", "body": "", "is_past": True, "is_fresh": False}
    with pytest.raises(BrokerValidationError) as exc:
        require_gate("drop table t", "56485", "goumlynecruxrlmzlntp", cur=FakeCursor(row), migration="435")
    assert exc.value.code == ErrorCode.GATE_INVALID
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_HAZ, "56485", _SYNTH_REF, cur=FakeCursor({**_bytebound_row(), "is_fresh": False}),
                     migration="435")
    assert exc.value.code == ErrorCode.GATE_INVALID


def test_b3_max_age_is_computed_by_the_db_clock_with_a_24h_default():
    cur = FakeCursor(_ref_row())
    require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=cur, ref="op#36027")
    sql, params = cur.calls[0]
    assert "make_interval(secs => %s)" in sql and "now()" in sql
    assert params == (DEFAULT_GATE_MAX_AGE_SECONDS, _EPOCH_FLOOR, 83934)
    assert DEFAULT_GATE_MAX_AGE_SECONDS == 86400


def test_b3_configured_max_age_is_passed_to_the_db():
    cur = FakeCursor(_ref_row())
    require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=cur, ref="op#36027", gate_max_age_seconds=3600)
    assert cur.calls[0][1] == (3600, _EPOCH_FLOOR, 83934)


@pytest.mark.parametrize("bad", [0, -1, True, "86400", 1.5, 72 * 3600 + 1, 10 ** 9])
def test_b3_misconfigured_max_age_refuses_every_gate_before_the_bus(bad):
    cur = FakeCursor(_ref_row())
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=cur, ref="op#36027", gate_max_age_seconds=bad)
    assert exc.value.code == ErrorCode.GATE_INVALID
    assert cur.calls == []


@pytest.mark.parametrize("gid", ["083934", "00083934", "0"])
def test_b3_non_canonical_gate_id_refused_before_the_bus(gid):
    # "0" is canonical digits but never a real bus id; it reaches the lookup and
    # is refused there as not-found. Leading-zero spellings never reach the bus.
    cur = FakeCursor(None)
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, gid, _SYNTH_REF, cur=cur, ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID
    if gid != "0":
        assert cur.calls == []
        assert "canonical" in exc.value.message


def test_b3_plain_read_never_checks_freshness():
    cur = FakeCursor(_ref_row(is_fresh=False))
    assert require_gate("select 1", "83934", _SYNTH_REF, cur=cur) is False
    assert cur.calls == []


# --- B3 rulings (console #89827/#89841): reasons, 72h ceiling, cutover floor -

_QA = "oovskwogvefnznfowjky"


def test_b3_stale_gate_carries_reason_gate_too_old():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row(is_fresh=False)), ref="op#36027")
    assert exc.value.code == ErrorCode.GATE_INVALID
    assert exc.value.reason == "gate_too_old"


def test_b3_max_age_bounds_are_hard_coded_1s_to_72h():
    assert MIN_GATE_MAX_AGE_SECONDS == 1 and MAX_GATE_MAX_AGE_SECONDS == 72 * 3600
    for ok in (1, 3600, 72 * 3600):
        assert require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row()),
                            ref="op#36027", gate_max_age_seconds=ok) is True
    for bad in (0, -1, 72 * 3600 + 1):
        with pytest.raises(BrokerValidationError) as exc:
            require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row()),
                         ref="op#36027", gate_max_age_seconds=bad)
        assert exc.value.code == ErrorCode.GATE_INVALID
        assert exc.value.reason == "gate_config_invalid"


def test_b3_no_cutover_floor_refuses_gates_on_non_qa_refs_before_the_bus():
    for ref in (_SYNTH_REF, _GOUMLYNE, "ceayjeamtmcyzzvqflus"):
        cur = FakeCursor(_ref_row(body=f"op#36027 on {ref}"))
        with pytest.raises(BrokerValidationError) as exc:
            _raw_require_gate(_DML_UPDATE, "83934", ref, cur=cur, ref="op#36027")
        assert exc.value.code == ErrorCode.GATE_INVALID
        assert exc.value.reason == "gate_cutover_unset"
        assert cur.calls == []


def test_b3_no_cutover_floor_still_allows_qa_gates():
    cur = FakeCursor(_ref_row(body="GO op#36027 on qa", after_cutover=None))
    assert _raw_require_gate(_DML_UPDATE, "83934", _QA, cur=cur, ref="op#36027") is True
    assert cur.calls[0][1] == (86400, None, 83934)


def test_b3_no_cutover_floor_never_affects_plain_reads():
    cur = FakeCursor()
    assert _raw_require_gate("select 1", None, _GOUMLYNE, cur=cur) is False
    assert cur.calls == []


def test_b3_gate_created_before_the_cutover_floor_is_refused():
    floor = datetime(2026, 11, 5, tzinfo=timezone.utc)
    for row in (_ref_row(after_cutover=False), _ref_row(after_cutover=None)):
        cur = FakeCursor(row)
        with pytest.raises(BrokerValidationError) as exc:
            _raw_require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=cur, ref="op#36027", gate_not_before=floor)
        assert exc.value.code == ErrorCode.GATE_INVALID
        assert exc.value.reason == "gate_before_cutover"
        assert cur.calls[0][1] == (86400, floor, 83934)
    # also on QA, once a floor IS configured
    with pytest.raises(BrokerValidationError) as exc:
        _raw_require_gate(_DML_UPDATE, "83934", _QA,
                          cur=FakeCursor(_ref_row(body="op#36027 on qa", after_cutover=False)),
                          ref="op#36027", gate_not_before=floor)
    assert exc.value.reason == "gate_before_cutover"


def test_b3_gate_after_the_cutover_floor_passes():
    floor = datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert _raw_require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row()),
                             ref="op#36027", gate_not_before=floor) is True


def test_b3_naive_or_non_datetime_floor_is_refused_as_config_invalid():
    for bad in (datetime(2026, 11, 5), "2026-11-05T00:00:00Z"):
        cur = FakeCursor(_ref_row())
        with pytest.raises(BrokerValidationError) as exc:
            _raw_require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=cur, ref="op#36027", gate_not_before=bad)
        assert exc.value.reason == "gate_config_invalid"
        assert cur.calls == []


def test_b3_non_b3_refusals_carry_no_reason():
    with pytest.raises(BrokerValidationError) as exc:
        require_gate(_DML_UPDATE, "83934", _SYNTH_REF, cur=FakeCursor(_ref_row(from_agent="x")), ref="op#36027")
    assert exc.value.reason is None
