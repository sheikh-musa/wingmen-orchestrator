"""tests/test_hub_self_recovery.py — hub-local wedge-recovery decision core (op#42896
follow-on, CAI-RESP-1439, bus #43161). See nervous_system/hub_self_recovery.py's
module docstring for full provenance and the exact binding conditions each test here
verifies.
"""
from __future__ import annotations

import datetime
import os

import pytest

from nervous_system import hub_self_recovery as hsr


# --------------------------------------------------------------------------- #
# PURE core — lease_is_self, kill_switch_enabled, pending_work_verdict, wedge_detected
# --------------------------------------------------------------------------- #

def test_lease_is_self_true_only_for_gzbai_holder():
    assert hsr.lease_is_self("gzbai") is True
    assert hsr.lease_is_self("gzb") is True          # hub_reach alias
    assert hsr.lease_is_self("192.168.1.114") is True  # hub_reach alias


def test_lease_is_self_false_for_foreign_or_unknown_holder():
    assert hsr.lease_is_self("wingmen-core") is False
    assert hsr.lease_is_self(None) is False
    assert hsr.lease_is_self("some-other-host") is False


def test_kill_switch_default_enabled():
    assert hsr.kill_switch_enabled(file_present=False, db_enabled=True) is True


def test_kill_switch_db_missing_row_fails_open_to_enabled():
    # pre-migration / table not yet applied: must not silently disable the
    # predicate's own correctness — a real False always still wins (below).
    assert hsr.kill_switch_enabled(file_present=False, db_enabled=None) is True


def test_kill_switch_file_present_disables_regardless_of_db():
    assert hsr.kill_switch_enabled(file_present=True, db_enabled=True) is False


def test_kill_switch_db_false_disables_regardless_of_file():
    assert hsr.kill_switch_enabled(file_present=False, db_enabled=False) is False


def test_kill_switch_both_off_disables():
    assert hsr.kill_switch_enabled(file_present=True, db_enabled=False) is False


def test_pending_work_prefers_operator_log_when_both_present():
    v = hsr.pending_work_verdict(operator_unprocessed=[(101, "t")], stale_p1_bus_rows=[(202, "t")])
    assert v == {"pending": True, "kind": "operator_log", "row_id": 101}


def test_pending_work_uses_bus_when_only_bus_present():
    v = hsr.pending_work_verdict(operator_unprocessed=[], stale_p1_bus_rows=[(202, "t")])
    assert v == {"pending": True, "kind": "bus_p1", "row_id": 202}


def test_pending_work_false_when_neither_present():
    v = hsr.pending_work_verdict(operator_unprocessed=[], stale_p1_bus_rows=[])
    assert v == {"pending": False, "kind": None, "row_id": None}


def test_wedge_detected_menu_refuses_even_with_pending_work():
    pending = {"pending": True, "kind": "operator_log", "row_id": 1}
    ok, reason = hsr.wedge_detected(pending=pending, busy=False, menu=True, composer_empty=False)
    assert ok is False
    assert "menu" in reason


def test_wedge_detected_busy_refuses_even_with_pending_work():
    pending = {"pending": True, "kind": "operator_log", "row_id": 1}
    ok, reason = hsr.wedge_detected(pending=pending, busy=True, menu=False, composer_empty=False)
    assert ok is False
    assert "busy" in reason


def test_wedge_detected_menu_wins_over_busy_too():
    # both refuses present — either wording is acceptable, but it must still refuse.
    pending = {"pending": True, "kind": "operator_log", "row_id": 1}
    ok, _ = hsr.wedge_detected(pending=pending, busy=True, menu=True, composer_empty=False)
    assert ok is False


def test_wedge_detected_no_pending_work_is_healthy_todays_case():
    """The EXACT case orch-console named in bus #43161: 22.5h idle, only P2 unread
    (so pending_work_verdict already returned pending=False) -> no action, no matter
    how long the hub has been idle. Idle-with-nothing-pending must never be a wedge."""
    pending = {"pending": False, "kind": None, "row_id": None}
    ok, reason = hsr.wedge_detected(pending=pending, busy=False, menu=False, composer_empty=False)
    assert ok is False
    assert "healthy" in reason


def test_wedge_detected_pending_but_empty_composer_is_not_the_signature():
    pending = {"pending": True, "kind": "bus_p1", "row_id": 9}
    ok, reason = hsr.wedge_detected(pending=pending, busy=False, menu=False, composer_empty=True)
    assert ok is False
    assert "not the wedge signature" in reason


def test_wedge_detected_true_when_all_conditions_align():
    pending = {"pending": True, "kind": "operator_log", "row_id": 55}
    ok, reason = hsr.wedge_detected(pending=pending, busy=False, menu=False, composer_empty=False)
    assert ok is True
    assert "operator_log#55" in reason


def test_fixed_payload_is_a_plain_hardcoded_string_never_a_template():
    # condition (d): never operator words, never authorization content, never
    # templated from row content — asserted at the type/shape level.
    assert isinstance(hsr.FIXED_RESUBMIT_PAYLOAD, str)
    assert "{" not in hsr.FIXED_RESUBMIT_PAYLOAD and "%" not in hsr.FIXED_RESUBMIT_PAYLOAD
    assert hsr.FIXED_RESUBMIT_PAYLOAD == "reconcile your inbox"


# --------------------------------------------------------------------------- #
# evaluate() — wired orchestration, DB layer replaced via seam monkeypatches
# (not a low-level psycopg mock — each of hsr's own DB wrapper functions is a
# named, independently-testable seam; evaluate()'s job under test is SEQUENCING
# and ROUTING, not SQL correctness, which the pure functions above already cover).
# --------------------------------------------------------------------------- #

class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self):
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    """Patch every DB-touching seam to controllable in-memory fakes; ORCH_BODY_ROLE
    pinned to 'hub' (the guard this script requires); lease resolves to self by
    default. Returns a dict of mutable knobs the test tweaks before calling evaluate()."""
    monkeypatch.setenv("ORCH_BODY_ROLE", "hub")
    monkeypatch.setattr(hsr, "_connect", lambda: _FakeConn())
    monkeypatch.setattr(hsr.hub_reach, "read_holder_host", lambda conn: "gzbai")
    monkeypatch.setattr(hsr, "fetch_kill_switch_db_enabled", lambda conn, cur: True)
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [])
    monkeypatch.setattr(hsr, "fetch_stale_p1_bus_rows", lambda cur, max_age_s=900, limit=5: [])
    monkeypatch.setattr(hsr, "recent_action_count", lambda cur, since: 0)
    monkeypatch.setattr(hsr, "row_lifetime_count", lambda cur, triggering_row_id, pending_kind: 0)
    logged = []
    monkeypatch.setattr(hsr, "log_event",
                         lambda cur, **kw: logged.append(kw) or 1)
    monkeypatch.setattr(hsr, "_page_observation", lambda cur, **kw: None)
    return {"logged": logged}


def test_evaluate_refuses_when_not_hub_role(monkeypatch, wired):
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="observe")
    assert result["act"] is False
    assert "ORCH_BODY_ROLE" in result["reason"]


def test_evaluate_refuses_when_lease_not_self(monkeypatch, wired):
    monkeypatch.setattr(hsr.hub_reach, "read_holder_host", lambda conn: "wingmen-core")
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="act")
    assert result["act"] is False
    assert "orch_lease" in result["reason"]


def test_evaluate_refuses_when_kill_switch_off(monkeypatch, wired):
    monkeypatch.setattr(hsr, "fetch_kill_switch_db_enabled", lambda conn, cur: False)
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [(1, "t")])
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="act")
    assert result["act"] is False
    assert "kill switch" in result["reason"]


def test_evaluate_healthy_idle_no_pending_logs_nothing(wired):
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="observe")
    assert result["act"] is False
    assert wired["logged"] == []


def test_evaluate_observe_mode_never_acts_on_a_genuine_detection(monkeypatch, wired):
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [(7, "t")])
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="observe")
    assert result["act"] is False
    assert len(wired["logged"]) == 1
    assert wired["logged"][0]["action"] == "would-nudge"
    assert wired["logged"][0]["mode"] == "observe"
    assert wired["logged"][0]["triggering_row_id"] == 7


def test_evaluate_act_mode_acts_on_a_genuine_detection_under_the_limits(monkeypatch, wired):
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [(7, "t")])
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="act")
    assert result["act"] is True
    assert result["payload"] == hsr.FIXED_RESUBMIT_PAYLOAD
    assert wired["logged"][0]["action"] == "nudged"


def test_evaluate_rate_limited_never_acts_even_in_act_mode(monkeypatch, wired):
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [(7, "t")])
    monkeypatch.setattr(hsr, "recent_action_count", lambda cur, since: hsr._RATE_LIMIT_PER_HOUR)
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="act")
    assert result["act"] is False
    assert wired["logged"][0]["action"] == "rate-limited"


def test_evaluate_lifetime_ceiling_never_acts_even_under_the_hourly_limit(monkeypatch, wired):
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [(7, "t")])
    monkeypatch.setattr(hsr, "row_lifetime_count",
                         lambda cur, triggering_row_id, pending_kind: hsr._ROW_LIFETIME_CEILING)
    result = hsr.evaluate(busy=False, menu=False, composer_empty=False, mode="act")
    assert result["act"] is False
    assert wired["logged"][0]["action"] == "ceiling-reached"


def test_evaluate_busy_refuses_before_any_db_pending_check_matters(monkeypatch, wired):
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [(7, "t")])
    result = hsr.evaluate(busy=True, menu=False, composer_empty=False, mode="act")
    assert result["act"] is False
    assert "busy" in result["reason"]
    assert wired["logged"] == []


def test_evaluate_menu_refuses_before_any_db_pending_check_matters(monkeypatch, wired):
    monkeypatch.setattr(hsr, "fetch_operator_unprocessed", lambda limit=5: [(7, "t")])
    result = hsr.evaluate(busy=False, menu=True, composer_empty=False, mode="act")
    assert result["act"] is False
    assert "menu" in result["reason"]
    assert wired["logged"] == []
