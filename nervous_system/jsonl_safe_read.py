"""Defensive IO helpers for jsonl files per CAI-RESP-164 R3.

Read failures NEVER raise. None on any error. Watchdog must keep running
even when jsonl files are missing, rotated, or corrupt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SafeStats:
    size_bytes: int
    mtime: float


def safe_file_stats(path: Path) -> Optional[SafeStats]:
    try:
        st = path.stat()
        return SafeStats(size_bytes=st.st_size, mtime=st.st_mtime)
    except (OSError, FileNotFoundError):
        return None


def _extract_text(content) -> Optional[str]:
    """Claude CLI stores user content as either string or list-of-blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
    return None


def read_first_user_message(path: Path) -> Optional[str]:
    """Read the first {type:'user'} message's content text from a Claude CLI jsonl.

    Returns the text payload or None on ANY error (missing, empty, corrupt,
    no user message, content shape unrecognized).
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return None
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "user":
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                return _extract_text(msg.get("content"))
        return None
    except (OSError, FileNotFoundError):
        return None
