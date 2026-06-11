"""CADENCE-008 A per-cycle work report → agent_messages (from_agent='substrate')."""
from __future__ import annotations

FROM_AGENT = "substrate"
SUB_TAG = "substrate-ihsanos-drain"


def build_report_row(*, summary: str, report_only: bool) -> dict:
    prefix = "[REPORT-ONLY] " if report_only else ""
    return {
        "from_agent": FROM_AGENT,
        "to_agent": "cai",
        "message_type": "update",
        "subject": f"{prefix}ihsanos-drain cycle: {summary[:80]}",
        "body": summary[:4000],
        "requires_response": False,
        "is_test": False,
        "from_agent_verified": False,
        "sub_tag": SUB_TAG,
        "priority": "P3",
    }
