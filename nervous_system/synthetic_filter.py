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


def _filter_enabled() -> bool:
    """Kill-switch. Set ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED=false to bypass
    the filter entirely and revert to PR #28-only behavior. Default: true."""
    return os.environ.get("ORCHESTRATOR_SYNTHETIC_FILTER_ENABLED", "true").lower() \
        not in ("false", "0", "no", "off")


def _filter_mode() -> Literal["shadow", "enforce"]:
    """Mode toggle. Default 'shadow' — classify and log only, do not block
    dispatch. Set ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE=true to flip to
    'enforce' — classify, log, AND set bug_reports.status='rejected'."""
    if os.environ.get("ORCHESTRATOR_SYNTHETIC_FILTER_ENFORCE", "false").lower() \
            in ("true", "1", "yes", "on"):
        return "enforce"
    return "shadow"


import json
import logging

logger = logging.getLogger("wingmen.synthetic_filter")

_DECISION_REF = "BUG-PIPELINE-SYNTHETIC-FILTER-001"
_REJECTED_BY = "cc-orchestrator-filter"


async def apply_classification(
    supabase,
    bug: dict,
    classification: SyntheticClassification,
    mode: Literal["shadow", "enforce"],
) -> None:
    """Apply a classification result.

    - Writes a notification_log entry (always, both modes).
    - In enforce mode: ALSO updates bug_reports.status='rejected' + audit fields.
      (Enforce path lands in Task 7 — this commit is shadow path only.)
    - boot_briefing counters are computed by the view from notification_log
      on read — no Python-side counter writes needed.
    """
    bug_id = bug["id"]
    desc_excerpt = (bug.get("description") or "")[:80]

    log_payload = {
        "bug_id": bug_id,
        "rule": classification.rule,
        "matched_text": classification.matched_text,
        "mode": mode,
        "would_reject": True,
        "reporter_name": bug.get("reporter_name"),
        "description_excerpt": desc_excerpt,
    }

    await supabase.table("notification_log").insert({
        "source": "synthetic_filter",
        "decision_ref": _DECISION_REF,
        "channel": "bug_reports",
        "recipient": bug_id,
        "message_text": json.dumps(log_payload),
    }).execute()

    logger.info(
        f"synthetic_filter: bug {bug_id} classified rule={classification.rule} "
        f"mode={mode} reporter={bug.get('reporter_name')!r}"
    )
