"""CADENCE-008 A kill-switch: env-flag panic gate (mirrors cc_cai_daemon)."""
from __future__ import annotations

import os

PANIC_ENV_VAR = "WINGMEN_IHSANOS_DRAIN_DISABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def drain_disabled() -> bool:
    """True if the operator has tripped the kill switch. Default: enabled (False)."""
    return os.environ.get(PANIC_ENV_VAR, "").strip().lower() in _TRUTHY
