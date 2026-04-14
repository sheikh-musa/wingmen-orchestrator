"""Tests for error_tracker — verifies delegation to swallowed_except_harness."""

from unittest.mock import patch

from nervous_system import error_tracker
from nervous_system.swallowed_except_harness import SwallowedExceptCounter


def test_track_exception_records_in_counter():
    """track_exception should increment the counter for the given label."""
    fresh = SwallowedExceptCounter()

    with patch("nervous_system.swallowed_except_harness.counter", fresh):
        error_tracker.track_exception("test.module", ValueError("boom"))
        error_tracker.track_exception("test.module", ValueError("boom"))

    assert fresh.count("test.module") == 2


def test_track_exception_is_record_swallowed():
    """error_tracker.track_exception must be the same callable as record_swallowed."""
    from nervous_system.swallowed_except_harness import record_swallowed

    assert error_tracker.track_exception is record_swallowed


def test_check_swallowed_escalation_re_exported():
    """check_swallowed_escalation must be importable from error_tracker."""
    from nervous_system.swallowed_except_harness import check_swallowed_escalation

    assert error_tracker.check_swallowed_escalation is check_swallowed_escalation
