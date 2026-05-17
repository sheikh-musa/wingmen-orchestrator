"""Pure-unit tests for autonomous_loop_detector — filesystem scan logic.

No DB. Uses tmp_path to fabricate CC project dirs with fake jsonl files
at controlled mtimes, then asserts the detector's verdict matches.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.autonomous_loop_detector import (
    scan_one_directory,
    detect_active_loops,
    DETECTION_THRESHOLD_SESSIONS_24H,
)


def _make_jsonl_at(parent: Path, name: str, age_seconds: float) -> Path:
    p = parent / name
    p.write_text("{}")
    mtime = time.time() - age_seconds
    os.utime(p, (mtime, mtime))
    return p


class TestScanOneDirectory:
    def test_empty_dir_returns_zero(self, tmp_path):
        result = scan_one_directory(tmp_path)
        assert result["sessions_24h"] == 0
        assert result["cadence_seconds"] is None
        assert result["last_fire_at"] is None

    def test_single_file_returns_one_no_cadence(self, tmp_path):
        _make_jsonl_at(tmp_path, "a.jsonl", age_seconds=600)
        result = scan_one_directory(tmp_path)
        assert result["sessions_24h"] == 1
        assert result["cadence_seconds"] is None
        assert result["last_fire_at"] is not None

    def test_old_files_excluded_from_24h_window(self, tmp_path):
        _make_jsonl_at(tmp_path, "old.jsonl", age_seconds=2 * 86400)  # 2 days old
        _make_jsonl_at(tmp_path, "fresh.jsonl", age_seconds=600)  # 10 min old
        result = scan_one_directory(tmp_path)
        assert result["sessions_24h"] == 1

    def test_cadence_computed_from_multiple_files(self, tmp_path):
        # 5 files spaced ~5 min apart → median ~300s
        for i in range(5):
            _make_jsonl_at(tmp_path, f"s{i}.jsonl", age_seconds=i * 300 + 60)
        result = scan_one_directory(tmp_path)
        assert result["sessions_24h"] == 5
        assert result["cadence_seconds"] is not None
        # median should be ~300 (allow small fluctuation)
        assert 280 <= result["cadence_seconds"] <= 320, \
            f"expected ~300s, got {result['cadence_seconds']}"

    def test_non_jsonl_files_ignored(self, tmp_path):
        _make_jsonl_at(tmp_path, "real.jsonl", age_seconds=60)
        (tmp_path / "junk.txt").write_text("nope")
        (tmp_path / ".hidden").write_text("nope")
        result = scan_one_directory(tmp_path)
        assert result["sessions_24h"] == 1


class TestDetectActiveLoops:
    def test_below_threshold_not_flagged(self, tmp_path):
        cc_dir = tmp_path / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
        cc_dir.mkdir()
        # 10 sessions in last 24h — well below threshold of 50
        for i in range(10):
            _make_jsonl_at(cc_dir, f"s{i}.jsonl", age_seconds=i * 300 + 60)
        result = detect_active_loops(claude_projects_root=tmp_path)
        flagged = [r for r in result if r["cc_identity"] == "cc-scholar"]
        assert flagged == [], f"expected not flagged below threshold, got {flagged}"

    def test_above_threshold_flagged(self, tmp_path):
        cc_dir = tmp_path / "-Users-sheikhmusa-wingmen-projects-ai-scholar"
        cc_dir.mkdir()
        # 60 sessions in last 24h, ~5min cadence — should flag
        for i in range(60):
            _make_jsonl_at(cc_dir, f"s{i}.jsonl", age_seconds=i * 300 + 60)
        result = detect_active_loops(claude_projects_root=tmp_path)
        flagged = [r for r in result if r["cc_identity"] == "cc-scholar"]
        assert len(flagged) == 1
        row = flagged[0]
        assert row["sessions_24h"] == 60
        assert row["cadence_seconds"] is not None
        assert 280 <= row["cadence_seconds"] <= 320

    def test_unknown_directory_skipped(self, tmp_path):
        unknown = tmp_path / "-Users-sheikhmusa-other-project"
        unknown.mkdir()
        for i in range(100):
            _make_jsonl_at(unknown, f"s{i}.jsonl", age_seconds=i * 300 + 60)
        result = detect_active_loops(claude_projects_root=tmp_path)
        assert result == [], "unknown directories should be ignored"

    def test_multiple_families_flagged_independently(self, tmp_path):
        for proj, cc in [
            ("-Users-sheikhmusa-wingmen-projects-ai-scholar", "cc-scholar"),
            ("-Users-sheikhmusa-wingmen-projects-ihsanos", "cc-ihsanos"),
        ]:
            d = tmp_path / proj
            d.mkdir()
            for i in range(60):
                _make_jsonl_at(d, f"s{i}.jsonl", age_seconds=i * 300 + 60)
        result = detect_active_loops(claude_projects_root=tmp_path)
        ccs = sorted(r["cc_identity"] for r in result)
        assert ccs == ["cc-ihsanos", "cc-scholar"]

    def test_operator_session_dirs_flagged_with_operator_prefix(self, tmp_path):
        """Non-CC-family managed repos (per REPOS.json) — runaway pattern detection
        must still cover them via 'operator-<repo>' pseudo-identity. Prevents the
        cc-scholar-runaway-style burn from going invisible if it happens in dookana
        or cosem-adcda or other ad-hoc operator-session repo."""
        for proj, expected_id in [
            ("-Users-sheikhmusa-wingmen-projects-dookana", "operator-dookana"),
            ("-Users-sheikhmusa-wingmen-projects-cosem-adcda", "operator-cosem-adcda"),
            ("-Users-sheikhmusa-wingmen-projects-hifz-companion", "operator-hifz-companion"),
            ("-Users-sheikhmusa-wingmen-projects-fastrans", "operator-fastrans"),
        ]:
            d = tmp_path / proj
            d.mkdir()
            for i in range(60):
                _make_jsonl_at(d, f"s{i}.jsonl", age_seconds=i * 300 + 60)
        result = detect_active_loops(claude_projects_root=tmp_path)
        ids = sorted(r["cc_identity"] for r in result)
        assert ids == sorted([
            "operator-cosem-adcda",
            "operator-dookana",
            "operator-fastrans",
            "operator-hifz-companion",
        ]), f"all 4 operator-session repos should be flagged, got {ids}"


def test_detection_threshold_default_50():
    """Threshold ratified at 50 per CAI-RESP-157 Q2."""
    assert DETECTION_THRESHOLD_SESSIONS_24H == 50
