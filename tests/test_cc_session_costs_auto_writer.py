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


# ---------------------------------------------------------------------------
# Protected agents must be MEASURED, not silently dropped (cc-fleet-health
# bus #88345, 2026-11-01). The dynamic lane map used to exclude every
# protected base_agent_id, so the three protected agents with no static entry
# (cc-finance, cc-quality, cc-storefront) resolved to None and the writer
# skipped them at `if not cc_identity: continue` -- they read as zero spend.
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql if params is None else sql + " | params=%r" % (params,))

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cur


@pytest.fixture
def fleet_lanes_rows(monkeypatch):
    """Patch psycopg.connect so lane_dir_map_from_fleet_lanes reads given rows."""

    def _install(rows):
        import psycopg

        cur = _FakeCursor(rows)
        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn(cur))
        return cur

    return _install


def _worktree_for(dir_name: str) -> str:
    """A worktree path whose _mangle_worktree() output IS `dir_name`.

    Round-tripping through the mangling rule (rather than hardcoding a real
    checkout path) keeps the test about the mechanism: any path mangling to
    this ~/.claude/projects dir is the one the writer will see."""
    return "/" + dir_name.lstrip("-").replace("-", "/")


class TestProtectedAgentCoverage:
    """The three protected agents fleet_lanes knew about but the map hid."""

    @pytest.mark.parametrize("ident,dir_name", [
        ("cc-finance", "-Users-sheikhmusa-wingmen-projects-finance"),
        ("cc-quality", "-Users-sheikhmusa-wingmen-quality"),
        ("cc-storefront", "-Users-sheikhmusa-wingmen-projects-ihsanos-storefront"),
    ])
    def test_protected_lane_without_static_entry_resolves_via_dynamic_map(
            self, fleet_lanes_rows, ident, dir_name):
        from nervous_system.cc_session_costs_auto_writer import (
            lane_dir_map_from_fleet_lanes, resolve_cc_identity)
        fleet_lanes_rows([(ident, _worktree_for(dir_name))])
        extra = lane_dir_map_from_fleet_lanes("postgresql://fake/db")
        assert resolve_cc_identity(dir_name, extra) == ident

    def test_protected_lane_with_a_static_entry_still_wins_statically(self, fleet_lanes_rows):
        """Precedence: a stale fleet_lanes row can never shadow a canonical id."""
        from nervous_system.cc_session_costs_auto_writer import (
            lane_dir_map_from_fleet_lanes, resolve_cc_identity)
        fleet_lanes_rows([("some-other-agent", "/Users/sheikhmusa/wingmen/wingmen-cai")])
        extra = lane_dir_map_from_fleet_lanes("postgresql://fake/db")
        assert resolve_cc_identity("-Users-sheikhmusa-wingmen-wingmen-cai", extra) == "cai"

    def test_query_does_not_filter_protected_agents(self, fleet_lanes_rows):
        """Pins the fix at the SQL level: reverting the WHERE clause fails here."""
        from nervous_system.cc_session_costs_auto_writer import lane_dir_map_from_fleet_lanes
        cur = fleet_lanes_rows([("cc-finance", "/Users/sheikhmusa/wingmen/projects/finance")])
        lane_dir_map_from_fleet_lanes("postgresql://fake/db")
        assert cur.executed, "the map builder issued no query"
        sql = cur.executed[0]
        assert "protected" not in sql.lower()
        assert "ALL(" not in sql
        assert "worktree_path IS NOT NULL" in sql

    def test_the_cost_writer_is_the_only_consumer_of_this_map(self):
        """The whole safety argument for including protected agents.

        If a future KILL path (loop detector, watchdog, reaper) starts reading
        this map, the protected-agent exclusion has to come back FIRST. This
        fails the moment a second consumer appears.
        """
        root = Path(__file__).resolve().parent.parent
        allowed = {
            "nervous_system/cc_session_costs_auto_writer.py",  # definition
            "scripts/cc_session_costs_writer_run.py",          # the one consumer
            "tests/test_cc_session_costs_auto_writer.py",      # this file
        }
        hits = set()
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".claude/"):
                continue  # stale agent/worktree copies, not part of this tree
            if rel in allowed:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "lane_dir_map_from_fleet_lanes" in text:
                hits.add(rel)
        assert not hits, (
            "lane_dir_map_from_fleet_lanes gained a consumer outside the cost "
            "writer: %s -- protected agents must be taken back out of that map "
            "before it feeds anything on a kill path." % sorted(hits))
