"""Dispatch-time auto-reject filter for synthetic E2E test bug reports.

Per BUG-PIPELINE-SYNTHETIC-FILTER-001 + CAI-RESP-141. Two classification
rules (rule c dropped per CL1 — no repro_steps column on bug_reports).
Two env flags gate behavior: ENABLED (kill-switch) + ENFORCE (mode).

This module has two boundaries:
  - classify(bug) — pure function returning SyntheticClassification | None
  - apply_classification(...) — side-effecting; writes notification_log,
    updates bug_reports in enforce mode

Called from nervous_system/bug_reports_poll.py inside the per-bug loop.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal, Optional


# Rule (a): description matches "E2E test bug report" with optional trailing
# period and whitespace, case-insensitive. Anchored to whole-string.
_RULE_A_PATTERN = re.compile(r"^\s*E2E test bug report\.?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SyntheticClassification:
    """Result of classifying a bug_report row as synthetic-test-shaped."""
    rule: Literal["a_e2e_placeholder", "b_test_reporter"]
    matched_text: str
    reason: str = "synthetic_e2e_test"


def classify(bug: dict) -> Optional[SyntheticClassification]:
    """Classify a bug_report row against cai's two rules.

    Returns None if the bug does not match any rule (i.e. proceed to dispatch).
    Returns a SyntheticClassification if any rule matches.
    """
    description = (bug.get("description") or "").strip()
    reporter_name = bug.get("reporter_name") or ""

    # Rule (a): E2E placeholder phrase
    if _RULE_A_PATTERN.match(description):
        return SyntheticClassification(
            rule="a_e2e_placeholder",
            matched_text=description,
        )

    # Rule (b): reporter contains "(Test)" substring (case-sensitive parens)
    if "(Test)" in reporter_name:
        return SyntheticClassification(
            rule="b_test_reporter",
            matched_text=reporter_name,
        )

    return None
