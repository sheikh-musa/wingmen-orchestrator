"""INV-6 default HOLD + Q5 rail-(b) confidence-drop kill-switch.

Per CAI-RESP-185 Q5 rail (b): the kill-switch reverts cc-cai-daemon to
pure-escalation mode on confidence drop. This module owns the runtime
state machine that gates whether the daemon may act silently OR must
fall back to escalating everything.

Three states:
  STATE_LIVE                 — normal: silent-lane handlers may run + escalations push
  STATE_PURE_ESCALATION      — confidence-drop tripped: every classification escalates
                               regardless of label (silent-lane handlers blocked)
  STATE_PANIC_DISABLED       — env flag WINGMEN_CC_CAI_DAEMON_DISABLED=true: no actions
                               at all, including escalation. Operator hard override.

Trip conditions:
  - Env flag set at process start → STATE_PANIC_DISABLED (immediately)
  - N=3 consecutive classifier observations with confidence < 0.5 → STATE_PURE_ESCALATION
  - Operator-initiated force_state() call → arbitrary state with audit row

Every state transition writes an INV-5 audit row BEFORE the state change
takes effect (gates side effects on audit success).
"""
from __future__ import annotations

import logging
import os

from cc_cai_daemon.audit import AuditLogger

logger = logging.getLogger("cc_cai.kill_switch")

STATE_LIVE = "live"
STATE_PURE_ESCALATION = "pure_escalation_mode"
STATE_PANIC_DISABLED = "panic_disabled"

_VALID_STATES = frozenset({STATE_LIVE, STATE_PURE_ESCALATION, STATE_PANIC_DISABLED})

PANIC_ENV_VAR = "WINGMEN_CC_CAI_DAEMON_DISABLED"
CONFIDENCE_DROP_THRESHOLD = 0.5  # strictly less than → counts as low
CONFIDENCE_DROP_CONSECUTIVE = 3   # N in a row → trip


def _is_env_truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("true", "1", "yes", "on")


class KillSwitch:
    """Runtime state machine. The daemon checks should_act_silently() and
    should_escalate() before every action. INV-5 audit row on every transition.
    """

    def __init__(self, audit: AuditLogger):
        self._audit = audit
        self._low_conf_streak = 0
        if _is_env_truthy(os.environ.get(PANIC_ENV_VAR)):
            self._state = STATE_PANIC_DISABLED
            logger.warning(
                f"{PANIC_ENV_VAR} is set — cc-cai daemon starting in {STATE_PANIC_DISABLED}"
            )
        else:
            self._state = STATE_LIVE

    @property
    def state(self) -> str:
        return self._state

    def should_act_silently(self) -> bool:
        """True iff the daemon may run silent-lane handlers (mark_read, ack_fyi).
        False in pure_escalation or panic_disabled.
        """
        return self._state == STATE_LIVE

    def should_escalate(self) -> bool:
        """True iff the daemon may push escalations to Telegram.
        False only in panic_disabled.
        """
        return self._state in (STATE_LIVE, STATE_PURE_ESCALATION)

    def observe(self, confidence: float) -> None:
        """Update the consecutive-low-confidence counter; trip if threshold met.

        Called by the daemon main loop after every classifier observation,
        regardless of resulting label.
        """
        if self._state != STATE_LIVE:
            # Already tripped (pure_escalation or panic). Don't double-log.
            return
        if confidence < CONFIDENCE_DROP_THRESHOLD:
            self._low_conf_streak += 1
            if self._low_conf_streak >= CONFIDENCE_DROP_CONSECUTIVE:
                self._transition_to(
                    STATE_PURE_ESCALATION,
                    reason=(
                        f"confidence_drop_{CONFIDENCE_DROP_CONSECUTIVE}_consecutive_"
                        f"under_{CONFIDENCE_DROP_THRESHOLD}"
                    ),
                )
        else:
            self._low_conf_streak = 0

    def force_state(self, new_state: str, *, reason: str) -> None:
        """Operator-initiated transition. Audit-logged like automatic trips."""
        if new_state not in _VALID_STATES:
            raise ValueError(
                f"invalid state {new_state!r} — must be one of {sorted(_VALID_STATES)}"
            )
        self._transition_to(new_state, reason=reason)

    def _transition_to(self, new_state: str, *, reason: str) -> None:
        self._audit.log_kill_switch_trip(new_state=new_state, reason=reason)
        self._state = new_state
        logger.warning(f"kill_switch → {new_state}: {reason}")
