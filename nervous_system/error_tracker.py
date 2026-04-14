"""Error Tracker — one-liner callsite for swallowed-except blocks in poll modules.

Drop ``error_tracker.track_exception(label, exc)`` into any except block
where you log-and-continue. The harness counts repeated exceptions and
escalates to CTO via Telegram when a label fires more than N times in one hour.

Escalation is triggered by ``check_swallowed_escalation()`` which is called
from the orchestrator main loop — poll modules don't need to call it directly.

Usage::

    from nervous_system import error_tracker

    try:
        ...
    except Exception as e:
        logger.error(f"Something failed: {e}")
        error_tracker.track_exception("my_module.my_function", e)
"""

from __future__ import annotations

from nervous_system.swallowed_except_harness import (
    check_swallowed_escalation,
    record_swallowed as track_exception,
)

__all__ = ["track_exception", "check_swallowed_escalation"]
