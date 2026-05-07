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
