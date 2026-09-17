"""Pure-unit tests for cc_session_costs auto-writer."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone

from nervous_system.cc_session_costs_auto_writer import (
    parse_jsonl_usage,
    SessionTokens,
    sweep_projects_root,
    ended_at_for_row,
)


def _make_jsonl_with_ts(
    parent: Path,
    name: str,
    turns: list[tuple[str, str, dict | None]],
    mtime: float | None = None,
    trailing_coststate: bool = False,
) -> Path:
    """Write a jsonl whose real conversation turns carry a top-level ISO `timestamp`.

    `turns` = list of (timestamp, role, usage) where role is 'user'/'assistant';
    usage (assistant only) is the usage block or None. `trailing_coststate` appends
    a `{"type":"cost-state"}` record with NO top-level timestamp (the real-world
    trailing record that has no conversation ts)."""
    p = parent / name
    lines = []
    for ts, role, usage in turns:
        rec = {"type": role, "timestamp": ts, "message": {"role": role, "content": "x"}}
        if usage is not None:
            rec["message"]["usage"] = usage
        lines.append(json.dumps(rec))
    if trailing_coststate:
        lines.append(json.dumps({"type": "cost-state", "costUSD": 1.23}))
    p.write_text("\n".join(lines) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class TestLatestEventTs:
    """ended_at must derive from the MAX real in-jsonl event timestamp, NOT file mtime
    (cc_session_costs false-freshness bug, Nazim #40620/#40624)."""

    def test_latest_event_ts_is_max_real_turn_not_mtime(self, tmp_path):
        # Dead session: real turns 2 days old, but file mtime bumped to NOW.
        fresh_mtime = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        p = _make_jsonl_with_ts(tmp_path, "dead.jsonl", [
            ("2026-09-15T10:00:00.000Z", "user", None),
            ("2026-09-15T11:19:21.503Z", "assistant", {"input_tokens": 5, "output_tokens": 3}),
        ], mtime=fresh_mtime)
        result = parse_jsonl_usage(p)
        assert result.latest_event_ts == datetime(2026, 9, 15, 11, 19, 21, 503000, tzinfo=timezone.utc)
        # Prove it is NOT the (fresh) file mtime:
        assert result.latest_event_ts.date() != datetime(2026, 9, 17, tzinfo=timezone.utc).date()

    def test_latest_event_ts_live_session_is_fresh(self, tmp_path):
        p = _make_jsonl_with_ts(tmp_path, "live.jsonl", [
            ("2026-09-17T12:09:56.071Z", "user", None),
            ("2026-09-17T12:10:04.073Z", "assistant", {"input_tokens": 9, "output_tokens": 2}),
        ])
        result = parse_jsonl_usage(p)
        assert result.latest_event_ts == datetime(2026, 9, 17, 12, 10, 4, 73000, tzinfo=timezone.utc)

    def test_latest_event_ts_ignores_trailing_coststate_no_ts(self, tmp_path):
        # Real events + a trailing cost-state record with NO top-level timestamp.
        p = _make_jsonl_with_ts(tmp_path, "coststate.jsonl", [
            ("2026-09-15T10:00:00.000Z", "user", None),
            ("2026-09-15T11:19:21.503Z", "assistant", {"input_tokens": 1, "output_tokens": 1}),
        ], trailing_coststate=True)
        result = parse_jsonl_usage(p)
        # Falls back to the last REAL event, never the trailing no-ts record.
        assert result.latest_event_ts == datetime(2026, 9, 15, 11, 19, 21, 503000, tzinfo=timezone.utc)

    def test_latest_event_ts_none_when_no_real_events(self, tmp_path):
        p = tmp_path / "coststate-only.jsonl"
        p.write_text(json.dumps({"type": "cost-state", "costUSD": 1.0}) + "\n")
        result = parse_jsonl_usage(p)
        assert result.latest_event_ts is None


class TestSweepCarriesLatestEventTs:
    def test_sweep_row_includes_latest_event_ts(self, tmp_path):
        repo_dir = tmp_path / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
        repo_dir.mkdir()
        _make_jsonl_with_ts(repo_dir, "abc-123.jsonl", [
            ("2026-09-15T11:19:21.503Z", "assistant", {"input_tokens": 50, "output_tokens": 25}),
        ])
        rows = sweep_projects_root(tmp_path, modified_since=0.0)
        assert len(rows) == 1
        assert rows[0]["latest_event_ts"] == datetime(2026, 9, 15, 11, 19, 21, 503000, tzinfo=timezone.utc)


class TestEndedAtForRow:
    """The ended_at value that actually flows to the DB INSERT/UPDATE."""

    def test_ended_at_from_event_ts_never_mtime(self):
        # dead-fresh-mtime row: event ts old, mtime NOW -> ended_at = old event ts.
        old_dt = datetime(2026, 9, 15, 11, 19, 21, 503000, tzinfo=timezone.utc)
        fresh_mtime = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        row = {"latest_event_ts": old_dt, "mtime": fresh_mtime}
        assert ended_at_for_row(row) == old_dt

    def test_ended_at_none_when_no_event_ts_never_mtime(self):
        fresh_mtime = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        row = {"latest_event_ts": None, "mtime": fresh_mtime}
        # Never fall back to mtime; unknown -> None.
        assert ended_at_for_row(row) is None


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
