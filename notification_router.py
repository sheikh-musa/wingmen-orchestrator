"""Notification routing — single source of truth for admin chat targets."""

from __future__ import annotations

import os
from typing import Optional


def get_chat_id(category: str) -> Optional[str]:
    if category == "cto":
        return os.environ.get("CTO_GROUP_ID") or os.environ.get("MUSA_TELEGRAM_ID")
    if category == "ops":
        return os.environ.get("OPS_GROUP_ID") or os.environ.get("MUSA_TELEGRAM_ID")
    return os.environ.get("MUSA_TELEGRAM_ID")
