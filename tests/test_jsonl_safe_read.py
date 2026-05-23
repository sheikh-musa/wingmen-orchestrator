"""Pure-unit tests for jsonl_safe_read — verify never-raise + correct None-on-error."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.jsonl_safe_read import (
    read_first_user_message,
    safe_file_stats,
    SafeStats,
)


class TestReadFirstUserMessage:
    def test_missing_file_returns_none(self, tmp_path):
        result = read_first_user_message(tmp_path / "nope.jsonl")
        assert result is None

    def test_empty_file_returns_none(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert read_first_user_message(p) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "corrupt.jsonl"
        p.write_text("{this is not json}\n{also broken\n")
        assert read_first_user_message(p) is None

    def test_no_user_message_returns_none(self, tmp_path):
        p = tmp_path / "no-user.jsonl"
        p.write_text(json.dumps({"type": "summary", "summary": "x"}) + "\n")
        assert read_first_user_message(p) is None

    def test_returns_first_user_message_content(self, tmp_path):
        p = tmp_path / "ok.jsonl"
        msgs = [
            {"type": "summary", "summary": "x"},
            {"type": "user", "message": {"role": "user", "content": "hello"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "hi"}},
            {"type": "user", "message": {"role": "user", "content": "second"}},
        ]
        p.write_text("\n".join(json.dumps(m) for m in msgs) + "\n")
        assert read_first_user_message(p) == "hello"

    def test_user_message_with_content_blocks(self, tmp_path):
        """Claude CLI sometimes stores content as list-of-blocks instead of string."""
        p = tmp_path / "blocks.jsonl"
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "block-prompt"}],
            },
        }
        p.write_text(json.dumps(msg) + "\n")
        assert read_first_user_message(p) == "block-prompt"


class TestSafeFileStats:
    def test_missing_file_returns_none(self, tmp_path):
        assert safe_file_stats(tmp_path / "nope.jsonl") is None

    def test_returns_size_and_mtime(self, tmp_path):
        p = tmp_path / "f.jsonl"
        p.write_text("x" * 1024)
        stats = safe_file_stats(p)
        assert isinstance(stats, SafeStats)
        assert stats.size_bytes == 1024
        assert stats.mtime > 0
