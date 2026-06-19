"""operator_notify.py — deliver a GENUINE operator alert to @wingmennorchbot.

The migration target for the real signals that used to ride @ihsanosbot
(cai escalations, watchdog death-alerts, schema-gate migration blocks) after
ihsanosbot was silenced. Always reaches the operator's DM on the new bot via
scripts/tg_send.sh — so it inherits the 4096-char chunking AND the
operator_messages durable log, and it doesn't matter what legacy chat/token the
caller used.

Reserve for genuine, actionable signals (a fork, a death, a block) — noise
stays dead. See feedback_no_fake_autopilot / feedback_ping_operator_via_telegram.
"""
from __future__ import annotations
import pathlib
import subprocess

_ORCH = pathlib.Path(__file__).resolve().parent.parent
_TG_SEND = _ORCH / "scripts" / "tg_send.sh"


def notify_operator(text: str, tag: str = "fleet") -> bool:
    """Send `text` to the operator on @wingmennorchbot. Returns True on success.
    Safe to call from sync or async code (brief subprocess); never raises."""
    if not text:
        return False
    try:
        r = subprocess.run(
            [str(_TG_SEND), str(text), tag],
            capture_output=True, timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False
