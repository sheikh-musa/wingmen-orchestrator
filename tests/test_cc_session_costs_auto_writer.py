"""Pure-unit tests for cc_session_costs auto-writer."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.cc_session_costs_auto_writer import (
    parse_jsonl_usage,
    SessionTokens,
    sweep_projects_root,
)


def _make_jsonl(parent: Path, name: str, usages: list[dict], mtime: float | None = None) -> Path:
    """Write a jsonl with N assistant messages each carrying a usage block."""
    p = parent / name
    lines = []
    for u in usages:
        lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}))
        lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "x", "usage": u}}))
    p.write_text("\n".join(lines) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class TestParseJsonlUsage:
    def test_sums_input_output_across_assistant_messages(self, tmp_path):
        p = _make_jsonl(tmp_path, "sess.jsonl", [
            {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 1000},
            {"input_tokens": 10, "output_tokens": 30, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1500},
        ])
        result = parse_jsonl_usage(p)
        assert result.input_tokens == 110
        assert result.output_tokens == 80
        assert result.cache_creation_input_tokens == 200
        assert result.cache_read_input_tokens == 2500

    def test_missing_usage_fields_default_zero(self, tmp_path):
        p = _make_jsonl(tmp_path, "sess.jsonl", [
            {"input_tokens": 5, "output_tokens": 10},  # no cache fields
        ])
        result = parse_jsonl_usage(p)
        assert result.input_tokens == 5
        assert result.output_tokens == 10
        assert result.cache_creation_input_tokens == 0
        assert result.cache_read_input_tokens == 0

    def test_no_assistant_messages_returns_zeros(self, tmp_path):
        p = tmp_path / "user-only.jsonl"
        p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
        result = parse_jsonl_usage(p)
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    def test_corrupt_jsonl_returns_none(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text("not-json\n")
        result = parse_jsonl_usage(p)
        assert result is None

    def test_missing_file_returns_none(self, tmp_path):
        result = parse_jsonl_usage(tmp_path / "nope.jsonl")
        assert result is None


class TestSweepProjectsRoot:
    def test_attributes_unknown_dir_skipped(self, tmp_path):
        """Sweep must skip ~/.claude/projects/* directories not in _DIR_TO_CC."""
        unknown = tmp_path / "-some-random-dir"
        unknown.mkdir()
        (unknown / "sess.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}) + "\n")
        rows = sweep_projects_root(tmp_path, modified_since=0.0)
        assert rows == []

    def test_sweep_known_repo_emits_row(self, tmp_path):
        repo_dir = tmp_path / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
        repo_dir.mkdir()
        _make_jsonl(repo_dir, "abc-123.jsonl", [
            {"input_tokens": 50, "output_tokens": 25, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200},
        ])
        rows = sweep_projects_root(tmp_path, modified_since=0.0)
        assert len(rows) == 1
        row = rows[0]
        assert row["cc_identity"] == "cc-scholar"
        assert row["session_id"] == "abc-123"
        assert row["input_tokens"] == 50
        assert row["output_tokens"] == 25
        assert row["cache_creation_input_tokens"] == 100
        assert row["cache_read_input_tokens"] == 200

    def test_sweep_modified_since_filter_works(self, tmp_path):
        repo_dir = tmp_path / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
        repo_dir.mkdir()
        # File mtime in the past
        _make_jsonl(repo_dir, "old.jsonl", [{"input_tokens": 1, "output_tokens": 1}], mtime=100.0)
        rows = sweep_projects_root(tmp_path, modified_since=1000.0)
        assert rows == []  # older than cutoff
