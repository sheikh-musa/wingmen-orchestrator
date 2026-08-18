"""Tests for the fail-closed auth-admin-write gate
(scripts.lib.auth_write_gate) — CAI-RESP-1118 class-fix.

WHY (the incident this pins shut, 2026-08-18):
  cc-irsyad, doing a receipt-404 UI repro on the REAL irsyad silo (goumlyne
  `goumlynecruxrlmzlntp`), meant to touch sales@gazzabyte.sg but GoTrue's admin
  `/auth/v1/admin/users?email=` filter SILENTLY returned a DIFFERENT user; the
  lane acted WITHOUT asserting the resolved uid and overwrote a real staff
  password. Two `generate_link` calls it believed only *returned* links actually
  DISPATCHED real recovery emails to two real inboxes.

cai's spec (CAI-RESP-1118 item 5): no auth-admin-API write against a NON-QA
(real-client) org may proceed without BOTH (a) an ASSERTED uid-equality match and
(b) an EXPLICIT grant — fail-closed, enforced by construction, not by promise
([[feedback_enforce_process_in_code_not_promises]]).

These tests pin that contract with NO DB: the pure predicates plus the fail-closed
wrapper behaviour (grant + resolver injected).
"""
from datetime import datetime, timedelta, timezone

import pytest

from scripts.lib.auth_write_gate import (
    QA_TARGETS,
    authorize_auth_write,
    is_qa_target,
    uid_equality_ok,
)
from scripts.lib.require_verified_authorization import AuthResult

# ── registry refs (docs/data-store-registry.md) ──────────────────────────────
GOUMLYNE = "goumlynecruxrlmzlntp"      # irsyad silo — REAL donor/staff data
IHSANOS = "ceayjeamtmcyzzvqflus"       # ihsanos multi-tenant DB — REAL tenants
PERSONAL = "brrgastulcffamlbggyu"      # wingmen-personal — REAL, never demo
DEMO = "ywrpttpxwfcoodovxhsr"          # cosem demo/dev — synthetic only

REQUEST_TS = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
PHRASES = ["YES AUTH-WRITE"]
TOKENS = ["salsabiila"]
UID = "6760aba1-0000-0000-0000-000000000000"


# ── is_qa_target: deny-by-default classification ─────────────────────────────

def test_demo_project_is_qa():
    assert is_qa_target(DEMO, QA_TARGETS) is True


@pytest.mark.parametrize("ref", [GOUMLYNE, IHSANOS, PERSONAL])
def test_real_client_silos_are_never_qa(ref):
    assert is_qa_target(ref, QA_TARGETS) is False


def test_unknown_ref_is_not_qa_deny_by_default():
    assert is_qa_target("some-brand-new-project", QA_TARGETS) is False


@pytest.mark.parametrize("ref", [None, "", "   "])
def test_missing_ref_is_not_qa(ref):
    # A write whose target we cannot even name must never be classified QA.
    assert is_qa_target(ref, QA_TARGETS) is False


def test_qa_allowlist_is_not_env_overridable_by_default():
    # The pinned constant is the source of truth; an org-within-a-real-project
    # (e.g. "irsyad-qa") is NOT here because it shares the real project's
    # auth.users — QA for AUTH means a separate PROJECT.
    assert GOUMLYNE not in QA_TARGETS
    assert IHSANOS not in QA_TARGETS


# ── uid_equality_ok: the heart — defeats the silent-filter no-op ─────────────

def test_uid_equality_accepts_exact_single_match():
    ok, _ = uid_equality_ok([{"id": UID, "email": "salsabiila@x"}], UID)
    assert ok is True


def test_uid_equality_rejects_zero_rows_silent_noop():
    # The exact incident shape: filter returned nothing / no-opped.
    ok, reason = uid_equality_ok([], UID)
    assert ok is False and ("0" in reason or "no-op" in reason.lower() or "resolved no" in reason.lower())


def test_uid_equality_rejects_multiple_rows_ambiguous():
    ok, reason = uid_equality_ok(
        [{"id": UID}, {"id": "other-uid"}], UID
    )
    assert ok is False and "ambig" in reason.lower()


def test_uid_equality_rejects_wrong_user():
    # The filter silently returned a DIFFERENT user than the caller intended.
    ok, reason = uid_equality_ok([{"id": "a-different-real-uid"}], UID)
    assert ok is False and "!=" in reason


def test_uid_equality_rejects_missing_expected_uid():
    ok, reason = uid_equality_ok([{"id": UID}], None)
    assert ok is False and "expected_uid" in reason


# ── the wrapper: fail-closed on every uncertainty ────────────────────────────

def _grant_ok(*a, **k):
    return AuthResult(True, "granted", {"id": 999})


def _grant_deny(*a, **k):
    return AuthResult(False, "no bridge-verified authorization")


def _resolver_hit(dsn, selector):
    return [{"id": UID, "email": selector.get("value")}]


def _resolver_wrong(dsn, selector):
    return [{"id": "a-different-real-uid", "email": selector.get("value")}]


def _resolver_empty(dsn, selector):
    return []


def _call(**over):
    kw = dict(
        op_id="irsyad-pw-reset",
        operation="update_password",
        target_ref=GOUMLYNE,
        expected_uid=UID,
        selector={"by": "email", "value": "salsabiila@irsyad.edu.sg"},
        after=REQUEST_TS,
        approval_phrases=PHRASES,
        op_tokens=TOKENS,
        target_dsn="postgres://target",
        substrate_dsn="postgres://substrate",
        operator_chat_id="286619815",
        resolve_users=_resolver_hit,
        grant_fn=_grant_ok,
    )
    kw.update(over)
    return authorize_auth_write(**kw)


def test_qa_target_allowed_without_grant_or_uid():
    # Synthetic project: outside the freeze scope, permitted.
    r = _call(target_ref=DEMO, expected_uid=None, grant_fn=_grant_deny)
    assert r.ok is True and "qa" in r.reason.lower()


def test_real_target_with_grant_and_uid_match_allowed():
    assert _call().ok is True


def test_real_target_denied_without_grant():
    r = _call(grant_fn=_grant_deny)
    assert r.ok is False and "grant" in r.reason.lower()


def test_real_target_denied_on_uid_mismatch_even_with_grant():
    # The incident: a legit-looking selector, but it resolves to the WRONG user.
    r = _call(resolve_users=_resolver_wrong)
    assert r.ok is False and "uid" in r.reason.lower()


def test_real_target_denied_on_silent_noop_even_with_grant():
    r = _call(resolve_users=_resolver_empty)
    assert r.ok is False


def test_real_target_denied_without_expected_uid():
    r = _call(expected_uid=None)
    assert r.ok is False and "expected_uid" in r.reason


def test_real_target_denied_when_resolver_raises():
    def _boom(dsn, selector):
        raise RuntimeError("target DB unreachable")

    r = _call(resolve_users=_boom)
    assert r.ok is False and "fail-closed" in r.reason.lower()


def test_real_target_denied_without_target_dsn():
    r = _call(target_dsn=None)
    assert r.ok is False


def test_unknown_target_ref_treated_as_real_denied():
    # deny-by-default: an unrecognised project is NOT waved through as QA.
    r = _call(target_ref="brand-new-unknown-proj", grant_fn=_grant_deny)
    assert r.ok is False


def test_returns_auth_result_type():
    assert isinstance(_call(), AuthResult)
