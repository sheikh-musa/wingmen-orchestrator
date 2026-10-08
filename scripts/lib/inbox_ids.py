"""inbox_ids — the ONE source of truth for the id set a lane's inbox read/count
must cover (bus #51166 / #58271 / orch-console #59675 regression class).

A lane's mail can be addressed to either its BASE agent id (CC_BASE_AGENT_ID,
e.g. 'cc-cosem-adcda') or its INSTANCE/sub-tag id (e.g. 'cc-cosem-adcda-2').
adcda/platform instances relaunch with CC_BASE_OVERRIDE=cc-cosem-adcda, so they
run UNDER the base identity yet register a distinct sub-tag — any per-lane reader
that counts to_agent = base ALONE goes blind to the instance-addressed mail (the
exact shape that left operator rows — a client export fix, an Arabic correction —
unread on 'cc-cosem-adcda-2'). The fix everywhere is the same: read/count
to_agent IN (base, instance).

PURE + DEPENDENCY-FREE on purpose: this module imports nothing, so every caller
(nervous_system/* daemons, scripts/* watchdogs, shell heredocs) can import it
without dragging in deps. The session-specific instance RESOLUTION (which
sub-tag this pane is) is deliberately NOT here — it is a DB read each caller does
with the identity it has in hand (match agent_status on tmux_session so a SIBLING
instance's mail is never counted); this module only owns the id-SET shape.
"""
from __future__ import annotations


def inbox_ids(base: str, instance: str | None) -> list[str]:
    """The id set an inbox read/count for this lane must cover: its BASE id, and —
    when the lane runs as a distinct sub-tag/instance — that INSTANCE id too.

    Base-first and deduped. A SINGLETON lane (no distinct instance, or instance is
    None) returns exactly [base] — byte-identical to the old base-only path, so
    single-identity bodies (a cc-quality or a cai, with no sub-tag) are unaffected.

        inbox_ids("cc-cosem-adcda", "cc-cosem-adcda-2") -> ["cc-cosem-adcda", "cc-cosem-adcda-2"]
        inbox_ids("cc-cosem-exams", "cc-cosem-exams")   -> ["cc-cosem-exams"]  (singleton)
        inbox_ids("cc-cosem-exams", None)                -> ["cc-cosem-exams"]
    """
    if instance and instance != base:
        return [base, instance]
    return [base]
