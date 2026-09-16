"""Tests for scripts.lib.project_operator_authorization — the NON-money,
project-scoped authorization gate (op#20702 Stage C).

Same evidentiary shape as scripts.lib.require_verified_authorization (a
bridge-verified inbound operator_messages row, never a console/tmux claim),
but the authorizing chat_id(s) come from that PROJECT's registered operators
(project_governance.operators) instead of a single hardcoded MUSA_TELEGRAM_ID.
This gate is explicitly for a project's own BUILD/SCOPE decisions — it must
NEVER be used for money/irreversible ops, which stay on
require_verified_authorization.verified_authorization() (Musa-only)
unconditionally.
"""
from datetime import datetime, timedelta, timezone

import pytest

from scripts.lib.project_operator_authorization import (
    ProjectAuthResult,
    _fetch_operators_for_project,
    _is_authorizable_chat_id,
    find_verified_project_authorization,
    verified_project_authorization,
)

SHUQ = "605271890"
WAN = "661212242"
STRANGER = "999999999"
REQUEST_TS = datetime(2026, 9, 16, 8, 0, 0, tzinfo=timezone.utc)
PHRASES = ["APPROVED"]
TOKENS = ["fee-portal"]


def _row(**kw):
    base = dict(
        id=1,
        direction="inbound",
        channel="telegram",
        chat_id=SHUQ,
        text="APPROVED for the fee-portal build",
        created_at=REQUEST_TS + timedelta(minutes=5),
    )
    base.update(kw)
    return base


def _find(rows, operator_chat_ids=(SHUQ, WAN)):
    return find_verified_project_authorization(
        rows, operator_chat_ids=operator_chat_ids, approval_phrases=PHRASES,
        op_tokens=TOKENS, after=REQUEST_TS,
    )


# ── the happy path — ANY registered operator for the project may authorize ────

def test_valid_authorization_from_first_operator_accepted():
    assert _find([_row(chat_id=SHUQ)]) is not None


def test_valid_authorization_from_second_operator_accepted():
    """A project can have multiple operators (irsyad: Shuq + Wan) — either
    one's bridge-verified approval is sufficient; this is not a dual-sign."""
    assert _find([_row(chat_id=WAN)]) is not None


def test_returns_matching_row_for_audit():
    r = _find([_row(id=4242)])
    assert r["id"] == 4242


# ── the dangerous cases the gate MUST reject ──────────────────────────────────

def test_console_yes_is_not_in_operator_messages_at_all():
    assert _find([]) is None


def test_unregistered_chat_rejected():
    """A YES from anyone NOT in this project's registered operator list is
    not proof — even a real bridge-verified telegram row from a stranger."""
    assert _find([_row(chat_id=STRANGER)]) is None


def test_wrong_projects_operator_rejected():
    """An operator authorized for a DIFFERENT project (not in operator_chat_ids
    for THIS call) cannot authorize this project's decision — cross-project
    authority must never leak."""
    assert _find([_row(chat_id="-5390372474")], operator_chat_ids=(SHUQ, WAN)) is None


def test_before_request_rejected():
    assert _find([_row(created_at=REQUEST_TS - timedelta(minutes=1))]) is None


def test_at_request_instant_rejected():
    assert _find([_row(created_at=REQUEST_TS)]) is None


def test_outbound_row_rejected():
    assert _find([_row(direction="outbound")]) is None


def test_non_telegram_channel_rejected():
    assert _find([_row(channel="console")]) is None


def test_missing_approval_phrase_rejected():
    assert _find([_row(text="go ahead with the fee-portal build")]) is None


def test_missing_op_token_rejected():
    """Right phrase, but not referencing THIS decision."""
    assert _find([_row(text="APPROVED for the tabung report change")]) is None


def test_case_insensitive_match():
    assert _find([_row(text="approved for the FEE-PORTAL rollout")]) is not None


def test_empty_operator_list_rejects_everything():
    """A project with NO registered operators (project_governance.operators=[])
    has no one who can satisfy this gate — fail-closed, not a fallback to Musa
    (that would blur this gate's boundary with the money-only Musa gate)."""
    assert _find([_row(chat_id=SHUQ)], operator_chat_ids=()) is None


def test_first_valid_of_many_returned():
    rows = [
        _row(id=1, chat_id=STRANGER),
        _row(id=2, direction="outbound"),
        _row(id=3, chat_id=WAN),
    ]
    assert _find(rows)["id"] == 3


# ── fail-closed wrapper behaviour (DB-touching, mocked) ───────────────────────

def test_wrapper_bad_timestamp_fails_closed():
    res = verified_project_authorization(
        "irsyad", "op", after="not-a-timestamp", approval_phrases=PHRASES,
        op_tokens=TOKENS, dsn="postg://x")
    assert isinstance(res, ProjectAuthResult) and res.ok is False


def test_wrapper_missing_project_fails_closed():
    res = verified_project_authorization(
        None, "op", after=REQUEST_TS, approval_phrases=PHRASES,
        op_tokens=TOKENS, dsn="postg://x")
    assert res.ok is False and "project" in res.reason.lower()


def test_wrapper_missing_dsn_fails_closed(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    res = verified_project_authorization(
        "irsyad", "op", after=REQUEST_TS, approval_phrases=PHRASES,
        op_tokens=TOKENS, dsn=None)
    assert res.ok is False and "DSN" in res.reason


def test_wrapper_db_error_fails_closed():
    """An unreachable DB must DENY, never assume yes — same as
    require_verified_authorization's contract."""
    res = verified_project_authorization(
        "irsyad", "op", after=REQUEST_TS, approval_phrases=PHRASES,
        op_tokens=TOKENS, dsn="postgresql://nope:nope@127.0.0.1:1/nodb")
    assert res.ok is False


# ── R1 (op#20702 Stage A gate #40727): a group/channel chat_id can never authorize ──

def test_is_authorizable_chat_id_rejects_negative():
    """Telegram's convention: negative chat_id = group/channel, never a user."""
    assert _is_authorizable_chat_id("-5390147776") is False
    assert _is_authorizable_chat_id(-5390147776) is False


def test_is_authorizable_chat_id_accepts_positive():
    assert _is_authorizable_chat_id("1913044694") is True
    assert _is_authorizable_chat_id(1913044694) is True


def test_is_authorizable_chat_id_rejects_garbage():
    assert _is_authorizable_chat_id("not-a-number") is False
    assert _is_authorizable_chat_id(None) is False


def test_fetch_operators_excludes_group_ids(monkeypatch):
    """A project_governance.operators array containing a mistakenly-registered
    group id must NEVER surface that id as an authorizer — filtered at the
    fetch layer, by construction, not left to every caller to re-check."""
    import psycopg  # noqa: F401 — ensure the real module is importable/patchable

    class _FakeCursor:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, *a, **kw):
            pass
        def fetchone(self):
            return ([
                {"name": "Hariz", "chat_id": "1913044694"},
                {"name": "cosem-exams group (mistake)", "chat_id": "-5390372474"},
            ],)

    class _FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def cursor(self):
            return _FakeCursor()

    monkeypatch.setattr("psycopg.connect", lambda *a, **kw: _FakeConn())
    result = _fetch_operators_for_project("postg://x", "cosem")
    assert result == ["1913044694"]


def test_wrapper_unknown_project_fails_closed(monkeypatch):
    """A project with no project_governance row at all has no registered
    operators to check against — fail-closed, same as an empty operator list."""
    import scripts.lib.project_operator_authorization as mod

    monkeypatch.setattr(mod, "_fetch_operators_for_project", lambda dsn, project: [])
    res = verified_project_authorization(
        "unknown-project", "op", after=REQUEST_TS, approval_phrases=PHRASES,
        op_tokens=TOKENS, dsn="postg://x")
    assert res.ok is False and "operator" in res.reason.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
