"""long_caller_watchdog — Phase B kill-decision module per CAI-RESP-163.

Pure-Python decision logic. Does NOT execute SIGTERM directly — returns
KillDecision objects that the integration layer (watchdog.py) actuates.
This separation keeps kill-logic pure-unit-testable.

Hard-coded safety guards per CAI-RESP-163:
  C1: pre-kill registry re-query (race guard against just-registered callers)
  C2: SUBSTRATE_NATIVE_NEVER_KILL frozenset (belt-and-suspenders)
  Q3: parent_pid recycle race guard (re-verify cwd at kill time)
  Q-final: WINGMEN_LONG_CALLER_WATCHDOG_DISABLED env panic button

R1: Unregistered caller + observed cadence pattern → hard_kill
R3: Registered caller + cadence drift > 2x sustained over 30min → soft_alert
R4: Substrate-native → never_kill (hard-coded via SUBSTRATE_NATIVE_NEVER_KILL)
R2: Deferred per CAI-RESP-163 + CC-LONG-CALLER-AUTO-TOKEN-TRACK-001
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("wingmen.long_caller_watchdog")


# C2: substrate-native carve-out (hard-coded; cannot be overridden by registry)
SUBSTRATE_NATIVE_NEVER_KILL: frozenset[str] = frozenset({"ralphy", "paused-job-retry"})

# Q-final: panic button env flag
PANIC_ENV_VAR = "WINGMEN_LONG_CALLER_WATCHDOG_DISABLED"

# R3: cadence drift detection thresholds
CADENCE_DRIFT_MULTIPLIER = 2.0
CADENCE_OBSERVATION_WINDOW_SECONDS = 30 * 60
MIN_OBSERVATIONS_FOR_DRIFT = 5


@dataclass(frozen=True)
class KillDecision:
    """Result of decide_kill. Pure data; integration layer actuates."""
    action: str  # 'hard_kill' | 'soft_alert' | 'no_kill'
    reason: str
    caller_name: str
    pid: Optional[int] = None
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CadenceObservation:
    """One firing event observation for cadence-drift detection."""
    drift_detected: bool
    observed_cadence_seconds: Optional[float] = None
    sample_count: int = 0


class CadenceTracker:
    """In-memory sliding 30min window per caller for R3 drift detection.

    Per CAI-RESP-163 Q4 ratified: in-memory state in long-running watchdog
    process; restart resets window (acceptable — drift triggers only after
    30min observation).
    """

    def __init__(self, expected_cadence_seconds: float):
        self.expected = expected_cadence_seconds
        self._observations: dict[str, deque[float]] = defaultdict(lambda: deque())

    def observe(self, caller_name: str, observed_at: float) -> CadenceObservation:
        """Record a firing event. Returns CadenceObservation reflecting current state."""
        window = self._observations[caller_name]
        window.append(observed_at)
        cutoff = observed_at - CADENCE_OBSERVATION_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) < MIN_OBSERVATIONS_FOR_DRIFT:
            return CadenceObservation(drift_detected=False, sample_count=len(window))

        gaps = [window[i + 1] - window[i] for i in range(len(window) - 1)]
        if not gaps:
            return CadenceObservation(drift_detected=False, sample_count=len(window))
        avg_gap = sum(gaps) / len(gaps)
        drift = self.expected / avg_gap if avg_gap > 0 else float('inf')
        return CadenceObservation(
            drift_detected=(drift >= CADENCE_DRIFT_MULTIPLIER),
            observed_cadence_seconds=avg_gap,
            sample_count=len(window),
        )


def _resolve_pid_cwd(pid: int) -> Optional[str]:
    """Read process cwd via `ps -o cwd= -p <pid>`. None if dead/unresolvable."""
    try:
        proc = subprocess.run(
            ["ps", "-o", "cwd=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        if proc.returncode != 0:
            return None
        cwd = proc.stdout.strip()
        return cwd if cwd else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def decide_kill(
    *,
    caller_name: str,
    sessions_24h: int,
    cadence_seconds: int,
    registered: bool,
    registered_policy: Optional[str] = None,
    parent_pid: Optional[int] = None,
) -> KillDecision:
    """Pure decision function — given inputs, return KillDecision.

    Check order (CAI-RESP-163 mandate):
      1. C2 substrate-native frozenset — check FIRST
      2. Q-final panic button env flag
      3. Registry membership check (R4 if registered_policy='no_kill')
      4. R1 unregistered + pattern → hard_kill
    """
    if caller_name in SUBSTRATE_NATIVE_NEVER_KILL:
        return KillDecision(
            action="no_kill",
            reason="substrate_native_carve_out_C2",
            caller_name=caller_name,
            pid=parent_pid,
        )

    if os.environ.get(PANIC_ENV_VAR, "").lower() in ("true", "1", "yes", "on"):
        return KillDecision(
            action="no_kill",
            reason="panic_button_set",
            caller_name=caller_name,
            pid=parent_pid,
        )

    if registered:
        if registered_policy == "no_kill":
            return KillDecision(
                action="no_kill",
                reason="registered_no_kill_policy",
                caller_name=caller_name,
                pid=parent_pid,
            )
        return KillDecision(
            action="no_kill",
            reason="registered_no_R1_trigger",
            caller_name=caller_name,
            pid=parent_pid,
        )

    return KillDecision(
        action="hard_kill",
        reason="R1_unregistered_pattern",
        caller_name=caller_name,
        pid=parent_pid,
        extras={"sessions_24h": sessions_24h, "cadence_seconds": cadence_seconds},
    )


def decide_kill_with_pid_verify(
    *,
    caller_name: str,
    sessions_24h: int,
    cadence_seconds: int,
    registered: bool,
    registered_policy: Optional[str] = None,
    parent_pid: Optional[int] = None,
    expected_cwd_prefix: str = "/Users/sheikhmusa/wingmen/",
) -> KillDecision:
    """Wraps decide_kill with the Q3 PID-recycle race guard.

    Only relevant when decide_kill returns hard_kill — verifies PID's cwd
    still matches the expected prefix before approving the kill.
    """
    inner = decide_kill(
        caller_name=caller_name,
        sessions_24h=sessions_24h,
        cadence_seconds=cadence_seconds,
        registered=registered,
        registered_policy=registered_policy,
        parent_pid=parent_pid,
    )
    if inner.action != "hard_kill" or parent_pid is None:
        return inner
    observed_cwd = _resolve_pid_cwd(parent_pid)
    if observed_cwd is None or not observed_cwd.startswith(expected_cwd_prefix):
        return KillDecision(
            action="no_kill",
            reason=f"pid_recycled_or_unverifiable observed_cwd={observed_cwd!r}",
            caller_name=caller_name,
            pid=parent_pid,
            extras={"expected_cwd_prefix": expected_cwd_prefix, "observed_cwd": observed_cwd},
        )
    return inner


def build_telegram_body(
    *,
    event: str,
    caller_name: str,
    pid: int,
    cwd: str,
    sessions_24h: int,
    threshold: int,
) -> str:
    """Q7 amendment: required Telegram body shape with inline action menu."""
    if event == "hard_kill":
        return (
            "🛑 Watchdog killed unregistered caller\n\n"
            f"caller: {caller_name} (PID {pid}, cwd {cwd})\n"
            f"observed: {sessions_24h} sessions/24h (threshold {threshold})\n"
            "registry: NOT FOUND\n\n"
            "Next action — reply to this thread:\n"
            "  /legitimize → file CC-LONG-CALLER-INDIVIDUAL-NNN\n"
            "  /revoke    → confirm revocation, log to notification_log"
        )
    elif event == "soft_alert_cadence_drift":
        return (
            "⚠️ Watchdog soft alert — cadence drift\n\n"
            f"caller: {caller_name} (PID {pid}, cwd {cwd})\n"
            f"observed: {sessions_24h} sessions/24h\n"
            "soft alert: cadence drift > 2x expected over 30min window\n\n"
            "No SIGTERM — informational. Investigate via boot_briefing."
        )
    else:
        return f"Watchdog event ({event}) caller={caller_name} pid={pid}"
