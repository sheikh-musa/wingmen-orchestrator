"""Tests for the enforced pre-execution authorization gate
(scripts.lib.require_verified_authorization) — 2026-07-03 near-miss remediation.

The gate is what turns "six phantom YES-in-a-console" from a catastrophe into a
non-event: only a bridge-verified operator artifact can authorize an irreversible
op. These tests pin the fail-closed contract with NO DB (pure predicate) plus the
fail-closed wrapper behaviour.
"""
from datetime import datetime, timedelta, timezone

import pytest

from scripts.lib.require_verified_authorization import (
    AuthResult,
    find_verified_authorization,
    verified_authorization,
)

OPERATOR = "286619815"
REQUEST_TS = datetime(2026, 7, 3, 8, 0, 0, tzinfo=timezone.utc)
PHRASES = ["YES PURGE"]
# distinguishing SUBJECT token, not the verb — see purge runner OP_TOKENS note
TOKENS = ["irsyad"]


def _row(**kw):
    base = dict(
        id=1,
        direction="inbound",
        channel="telegram",
        chat_id=OPERATOR,
        text="YES PURGE the irsyad rows",
        created_at=REQUEST_TS + timedelta(minutes=5),
    )
    base.update(kw)
    return base


def _find(rows):
    return find_verified_authorization(
        rows, operator_chat_id=OPERATOR, approval_phrases=PHRASES,
        op_tokens=TOKENS, after=REQUEST_TS,
    )


# ── the happy path ────────────────────────────────────────────────────────────

def test_valid_bridge_authorization_accepted():
    assert _find([_row()]) is not None


def test_returns_matching_row_for_audit():
    r = _find([_row(id=4242)])
    assert r["id"] == 4242


# ── the dangerous cases the gate MUST reject ─────────────────────────────────

def test_console_yes_is_not_in_operator_messages_at_all():
    """A tmux/console YES never becomes an inbound telegram row — no candidate,
    no authorization. This is the exact 2026-07-03 failure mode."""
    assert _find([]) is None


def test_wrong_chat_id_rejected():
    """A YES from any chat other than the operator's real chat is not proof."""
    assert _find([_row(chat_id="999999999")]) is None


def test_before_request_rejected():
    """A stale/old YES (created before the op was requested) can't be replayed."""
    assert _find([_row(created_at=REQUEST_TS - timedelta(minutes=1))]) is None


def test_at_request_instant_rejected():
    """Must be strictly AFTER the request."""
    assert _find([_row(created_at=REQUEST_TS)]) is None


def test_outbound_row_rejected():
    """Our own outbound reply that quotes 'YES PURGE' is not an authorization."""
    assert _find([_row(direction="outbound")]) is None


def test_non_telegram_channel_rejected():
    """A hand-inserted row on some other channel is not a bridge artifact."""
    assert _find([_row(channel="console")]) is None


def test_missing_approval_phrase_rejected():
    assert _find([_row(text="go ahead and delete the irsyad rows")]) is None


def test_missing_op_token_rejected():
    """Right phrase, but not referencing THIS op."""
    assert _find([_row(text="YES PURGE the cosem cache")]) is None


def test_case_insensitive_match():
    assert _find([_row(text="yes purge the IRSYAD residency rows")]) is not None


def test_first_valid_of_many_returned():
    rows = [
        _row(id=1, chat_id="999"),           # wrong chat
        _row(id=2, direction="outbound"),    # outbound
        _row(id=3),                          # valid
    ]
    assert _find(rows)["id"] == 3


# ── fail-closed wrapper behaviour ────────────────────────────────────────────

def test_wrapper_bad_timestamp_fails_closed():
    res = verified_authorization(
        "op", after="not-a-timestamp", approval_phrases=PHRASES,
        op_tokens=TOKENS, operator_chat_id=OPERATOR, dsn="postg://x")
    assert isinstance(res, AuthResult) and res.ok is False


def test_wrapper_missing_operator_id_fails_closed(monkeypatch):
    monkeypatch.delenv("MUSA_TELEGRAM_ID", raising=False)
    res = verified_authorization(
        "op", after=REQUEST_TS, approval_phrases=PHRASES, op_tokens=TOKENS,
        operator_chat_id=None, dsn="postg://x")
    assert res.ok is False and "operator chat id" in res.reason


def test_wrapper_missing_dsn_fails_closed(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    res = verified_authorization(
        "op", after=REQUEST_TS, approval_phrases=PHRASES, op_tokens=TOKENS,
        operator_chat_id=OPERATOR, dsn=None)
    assert res.ok is False and "DSN" in res.reason


def test_wrapper_db_error_fails_closed():
    """An unreachable DB must DENY, never assume yes."""
    res = verified_authorization(
        "op", after=REQUEST_TS, approval_phrases=PHRASES, op_tokens=TOKENS,
        operator_chat_id=OPERATOR,
        dsn="postgresql://nope:nope@127.0.0.1:1/nodb")
    assert res.ok is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
