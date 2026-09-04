"""Tests for the CAI-1380 system-remediation identity one-off create script's
pure gate predicate (scripts.cai1380_create_system_remediation_identity).

Only the pure predicate (execution_ruling_ok) is unit-tested here — no DB, no
HTTP. It is deliberately fail-closed on every uncertainty, mirroring
uid_equality_ok's discipline in scripts/lib/auth_write_gate.py (CAI-RESP-1389's
own framing: "a distinct one-off process ALONGSIDE, not through,
authorize_auth_write() — but held to the same fail-closed bar").
"""
from datetime import datetime, timedelta, timezone

from scripts.cai1380_create_system_remediation_identity import (
    OP_ID,
    execution_ruling_ok,
)

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
CLOSED = NOW - timedelta(hours=1)  # challenge window already closed
OPEN = NOW + timedelta(hours=1)  # challenge window still open


def _row(**overrides):
    base = {
        "decision_ref": "CAI-RESP-9999",
        "title": f"CAI-1380 execution ruling naming {OP_ID}",
        "decision": "Execution GRANTED for the named script.",
        "status": "active",
        "challenge_status": "unchallenged",
        "challengeable_until": CLOSED,
    }
    base.update(overrides)
    return base


def test_no_row_denies():
    res = execution_ruling_ok(None, op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "no execution ruling found" in res.reason


def test_valid_row_with_closed_window_passes():
    res = execution_ruling_ok(_row(), op_id=OP_ID, now=NOW)
    assert res.ok is True


def test_open_challenge_window_denies():
    res = execution_ruling_ok(_row(challengeable_until=OPEN), op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "has not closed yet" in res.reason


def test_missing_challengeable_until_denies():
    res = execution_ruling_ok(_row(challengeable_until=None), op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "cannot confirm the 24h window closed" in res.reason


def test_superseded_status_denies():
    res = execution_ruling_ok(_row(status="superseded"), op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "no longer authorizing" in res.reason


def test_rejected_status_denies():
    res = execution_ruling_ok(_row(status="rejected"), op_id=OP_ID, now=NOW)
    assert res.ok is False


def test_open_challenge_status_denies():
    res = execution_ruling_ok(_row(challenge_status="challenged"), op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "under an open challenge" in res.reason


def test_ruling_not_referencing_op_id_denies():
    """A ruling that exists, is unchallenged, and has a closed window — but
    doesn't name THIS script — must not accidentally authorize it. This is
    the create-script equivalent of auth_write_gate's ambiguous-selector
    guard: don't let a different op's YES satisfy this one."""
    res = execution_ruling_ok(
        _row(title="Some other decision", decision="about something else entirely"),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is False
    assert "does not reference op_id" in res.reason


def test_iso_string_challengeable_until_is_parsed():
    res = execution_ruling_ok(
        _row(challengeable_until=CLOSED.isoformat().replace("+00:00", "Z")),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is True


def test_naive_datetime_challengeable_until_is_treated_as_utc():
    res = execution_ruling_ok(
        _row(challengeable_until=CLOSED.replace(tzinfo=None)),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is True
