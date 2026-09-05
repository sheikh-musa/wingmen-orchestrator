from ihsanos_drain.grant import (
    GRANTED,
    REFUSED_MIGRATION,
    REPORT_ONLY,
    evaluate_grant,
)


def _row(**kw):
    base = dict(
        execution_status="granted",
        repos_affected=["ihsanos"],
        challenge_status="accepted_by_timeout",
        decision="do the thing",
    )
    base.update(kw)
    return base


def test_granted_when_all_conditions_met():
    v = evaluate_grant(_row(), is_migration=False, migration_filename=None)
    assert v.status == GRANTED


def test_granted_for_each_closed_challenge_state():
    for cs in ("accepted", "accepted_by_timeout", "unchallenged"):
        v = evaluate_grant(
            _row(challenge_status=cs), is_migration=False, migration_filename=None
        )
        assert v.status == GRANTED, cs


def test_report_only_for_open_or_active_challenge_states():
    # window-open, actively challenged, superseded, or ambiguous => never execute
    for cs in ("challenge_window", "challenged", "superseded", "informational", None):
        v = evaluate_grant(
            _row(challenge_status=cs), is_migration=False, migration_filename=None
        )
        assert v.status == REPORT_ONLY, cs


def test_report_only_when_execution_status_not_granted():
    for s in ("implemented", "ip_gate_cleared", None, "archived"):
        v = evaluate_grant(
            _row(execution_status=s), is_migration=False, migration_filename=None
        )
        assert v.status == REPORT_ONLY


def test_report_only_when_not_ihsanos_executor():
    v = evaluate_grant(
        _row(repos_affected=["orchestrator"]), is_migration=False, migration_filename=None
    )
    assert v.status == REPORT_ONLY


def test_migration_refused_when_filename_not_named_in_decision():
    v = evaluate_grant(
        _row(decision="apply the schema change"),
        is_migration=True,
        migration_filename="20260612_add_x.sql",
    )
    assert v.status == REFUSED_MIGRATION


def test_migration_granted_when_filename_named_in_decision():
    v = evaluate_grant(
        _row(decision="apply 20260612_add_x.sql exactly"),
        is_migration=True,
        migration_filename="20260612_add_x.sql",
    )
    assert v.status == GRANTED
