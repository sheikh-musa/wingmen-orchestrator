"""Tests for the CAI-1380 system-remediation identity one-off create script's
pure gate predicate (scripts.cai1380_create_system_remediation_identity).

Only the pure predicate (execution_ruling_ok) is unit-tested here — no DB, no
HTTP. It is deliberately fail-closed on every uncertainty, mirroring
uid_equality_ok's discipline in scripts/lib/auth_write_gate.py (CAI-RESP-1389's
own framing: "a distinct one-off process ALONGSIDE, not through,
authorize_auth_write() — but held to the same fail-closed bar").

REV 2 (orch-console's PR#83 must-fix): the row shapes below are modeled on
the REAL strategic_decisions table, verified live before writing these tests
— NOT an assumed shape. Confirmed distinct values: status ∈ {active,
superseded} (only two observed — 'rejected'/'revoked' never occur);
challenge_status ∈ {accepted, accepted_by_audit, accepted_by_timeout,
challenge_window, informational, overridden, superseded, unchallenged}
('challenged' is not a real value). Confirmed against real rows:
CAI-BATCH-001 (status=active, challenge_status=superseded,
superseded_by_decision_ref=CAI-RESP-058) and CAI-RESP-711 (status=active,
challenge_status=superseded) — supersession recorded via challenge_status/
superseded_by_decision_ref while status STAYS active. Confirmed CAI-RESP-
1090/1097/1111 sit at challenge_status='challenge_window' with
challengeable_until already in the past — the window-close check MUST stay
time-based, challenge_status does not auto-flip on close.

REV 3 (cc-storefront's FULL-audit must-fix, decision_audits 312): REV 2 was
STILL fail-open — strategic_decisions has a SECOND, separate supersession
reference, `superseded_by` (BIGINT, the superseding row's numeric `id`),
distinct from `superseded_by_decision_ref` (TEXT). Confirmed live: 6 real
rows are superseded ONLY via the bigint FK — status='active',
challenge_status='accepted_by_timeout', text-ref NULL — e.g. CAI-RESP-1141
(id=2594, superseded_by=2596, which is CAI-RESP-1143's id).
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
    """A realistic 'ruling authorizes this script, window closed, no
    supersession' row — the real column set, real observed values."""
    base = {
        "decision_ref": "CAI-RESP-9999",
        "title": f"CAI-1380 execution ruling naming {OP_ID}",
        "decision": "Execution GRANTED for the named script.",
        "status": "active",
        "challenge_status": "accepted_by_timeout",
        "challengeable_until": CLOSED,
        "superseded_by_decision_ref": None,
        "superseded_by": None,
        "execution_status": "granted",
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
    """status='superseded' — the (rarer, but real) direct case."""
    res = execution_ruling_ok(_row(status="superseded"), op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "superseded/overridden" in res.reason


def test_status_active_but_challenge_status_superseded_denies():
    """THE REAL-WORLD SHAPE (orch-console's must-fix, verified against
    CAI-RESP-711/CAI-BATCH-001 live): status stays 'active' — the OLD check
    here (status in ('superseded','rejected','revoked')) would have WRONGLY
    PASSED this. Supersession is recorded via challenge_status, not status."""
    res = execution_ruling_ok(
        _row(status="active", challenge_status="superseded"),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is False
    assert "superseded/overridden" in res.reason


def test_superseded_by_decision_ref_set_denies_even_with_active_status():
    """Mirrors CAI-BATCH-001 exactly: status=active, superseded_by_decision_
    ref set. Must deny regardless of what status/challenge_status say."""
    res = execution_ruling_ok(
        _row(status="active", challenge_status="accepted", superseded_by_decision_ref="CAI-RESP-058"),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is False
    assert "superseded/overridden" in res.reason


def test_overridden_challenge_status_denies():
    res = execution_ruling_ok(_row(challenge_status="overridden"), op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "superseded/overridden" in res.reason


def test_bigint_superseded_by_denies_even_with_active_status_and_timeout_challenge():
    """THE cc-storefront FULL-audit catch (decision_audits 312), mirrors
    CAI-RESP-1141 exactly: status=active, challenge_status=accepted_by_
    timeout (NOT superseded/overridden), superseded_by_decision_ref NULL,
    but the BIGINT superseded_by column set to the superseding row's id.
    REV 2 alone would have WRONGLY PASSED this — none of its three checks
    (status, challenge_status, text-ref) see this column at all."""
    res = execution_ruling_ok(
        _row(status="active", challenge_status="accepted_by_timeout", superseded_by_decision_ref=None, superseded_by=2596),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is False
    assert "superseded/overridden" in res.reason


def test_execution_status_not_granted_denies_even_when_op_id_is_named():
    """REV 4 (orch-console's hardening of both auditors' minor note): a
    ruling that names this op_id but never actually grants it (e.g. still
    'approved', or a design/build-only ruling like CAI-RESP-1389 itself)
    must not pass just because the substring is present."""
    res = execution_ruling_ok(
        _row(execution_status="approved"),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is False
    assert "not 'granted'" in res.reason


def test_missing_execution_status_denies():
    res = execution_ruling_ok(_row(execution_status=None), op_id=OP_ID, now=NOW)
    assert res.ok is False
    assert "not 'granted'" in res.reason


def test_challenge_window_label_with_closed_timestamp_still_passes():
    """Mirrors CAI-RESP-1090/1097/1111 exactly: challenge_status never
    auto-flipped away from 'challenge_window' even though the window
    genuinely closed. The time-based check, not the label, must govern —
    denying this would be WRONGLY blocking a legitimately-closed ruling."""
    res = execution_ruling_ok(
        _row(challenge_status="challenge_window", challengeable_until=CLOSED),
        op_id=OP_ID,
        now=NOW,
    )
    assert res.ok is True


def test_ruling_not_referencing_op_id_denies():
    """A ruling that exists, isn't superseded, and has a closed window — but
    doesn't name THIS script — must not accidentally authorize it. This is
    the create-script equivalent of auth_write_gate's ambiguous-selector
    guard: don't let a different op's ruling satisfy this one."""
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
