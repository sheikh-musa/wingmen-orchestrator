"""Pure-unit tests for synthetic_filter.classify and mode helpers.

No DB. Per CAI-RESP-141: rule (c) dropped (no repro_steps column).
Two rules only — (a) E2E placeholder phrase, (b) reporter substring.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.synthetic_filter import SyntheticClassification, classify


class TestRuleA:
    """Rule (a): description ~* '^E2E test bug report\\.?\\s*$'"""

    def test_exact_phrase_no_period_classifies(self):
        bug = {"description": "E2E test bug report", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "a_e2e_placeholder"
        assert result.matched_text == "E2E test bug report"

    def test_exact_phrase_with_period_classifies(self):
        bug = {"description": "E2E test bug report.", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "a_e2e_placeholder"

    def test_case_insensitive(self):
        bug = {"description": "e2e TEST bug Report", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "a_e2e_placeholder"

    def test_trailing_whitespace_tolerated(self):
        bug = {"description": "E2E test bug report.   \n", "reporter_name": "real human"}
        result = classify(bug)
        assert result is not None

    def test_extra_text_after_phrase_does_not_match(self):
        bug = {"description": "E2E test bug report — actually broken", "reporter_name": "real human"}
        result = classify(bug)
        assert result is None


class TestRuleB:
    """Rule (b): reporter_name contains substring '(Test)' (parens included)."""

    def test_test_suffix_classifies(self):
        bug = {"description": "real bug", "reporter_name": "BAPA Admin (Test)"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "b_test_reporter"
        assert result.matched_text == "BAPA Admin (Test)"

    def test_test_prefix_classifies(self):
        bug = {"description": "real bug", "reporter_name": "(Test) Account"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "b_test_reporter"

    def test_test_middle_classifies(self):
        bug = {"description": "real bug", "reporter_name": "Foo (Test) Bar"}
        result = classify(bug)
        assert result is not None
        assert result.rule == "b_test_reporter"

    def test_test_word_without_parens_does_not_match(self):
        bug = {"description": "real bug", "reporter_name": "Test User"}
        assert classify(bug) is None

    def test_my_test_user_does_not_match(self):
        bug = {"description": "real bug", "reporter_name": "MyTest User"}
        assert classify(bug) is None

    def test_case_sensitive_parens(self):
        # Spec says "(Test)" with capital T — lower-case "(test)" should NOT match
        bug = {"description": "real bug", "reporter_name": "Foo (test) Bar"}
        assert classify(bug) is None


class TestRuleCDropped:
    """Rule (c) was dropped per CAI-RESP-141 CL1 (no repro_steps column).

    These tests assert that the patterns rule (c) WOULD have caught now
    pass through unclassified — protects against accidental rule (c)
    re-introduction.
    """

    def test_empty_description_does_not_classify(self):
        bug = {"description": "", "reporter_name": "real human"}
        assert classify(bug) is None

    def test_whitespace_only_description_does_not_classify(self):
        bug = {"description": "   \n  ", "reporter_name": "real human"}
        assert classify(bug) is None

    def test_none_fields_do_not_crash(self):
        bug = {"description": None, "reporter_name": None}
        assert classify(bug) is None

    def test_missing_fields_do_not_crash(self):
        bug = {}
        assert classify(bug) is None


class TestNormalBugs:
    """Realistic bug_report rows must classify as None."""

    def test_real_bug_with_full_description(self):
        bug = {
            "description": "When selecting a vehicle the menu is cutoff at the bottom",
            "reporter_name": "Mulifatullah Bin Atan",
        }
        assert classify(bug) is None

    def test_terse_real_bug(self):
        # Important: terse bugs are NOT synthetic. Rule (c) was dropped
        # specifically because it would have falsely flagged these.
        bug = {"description": "page crashes on load", "reporter_name": "musa"}
        assert classify(bug) is None


from nervous_system.synthetic_filter import _filter_enabled, _filter_mode


class TestModeHelpers:
    """ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED + ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE
    env flag resolution. Defaults: ENABLED=true, ENFORCE=false → shadow mode."""

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", raising=False)
        assert _filter_enabled() is True

    def test_default_mode_shadow(self, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", raising=False)
        assert _filter_mode() == "shadow"

    def test_disabled_via_false(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "false")
        assert _filter_enabled() is False

    def test_disabled_via_off(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "off")
        assert _filter_enabled() is False

    def test_disabled_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "FALSE")
        assert _filter_enabled() is False

    def test_enforce_when_true(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "true")
        assert _filter_mode() == "enforce"

    def test_enforce_when_one(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "1")
        assert _filter_mode() == "enforce"

    def test_unrecognized_value_treated_as_shadow(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "maybe")
        assert _filter_mode() == "shadow"
