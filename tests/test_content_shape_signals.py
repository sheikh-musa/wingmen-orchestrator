"""Pure-unit tests for content_shape_signals — three signal extractors."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.content_shape_signals import (
    signal_a_median_size,
    signal_b_cadence_band,
    signal_c_identical_prompts,
    SignalResult,
    SIGNAL_A_MAX_BYTES,
    SIGNAL_B_BAND_LO,
    SIGNAL_B_BAND_HI,
    SIGNAL_B_MIN_SPAN_SECONDS,
)


def _make_jsonl(parent: Path, name: str, size_bytes: int, mtime: float, first_user_text: str = "ok") -> Path:
    p = parent / name
    body = json.dumps({"type": "user", "message": {"role": "user", "content": first_user_text}})
    pad_needed = size_bytes - len(body) - 1
    if pad_needed > 0:
        body = body + "\n" + ("x" * pad_needed)
    p.write_text(body)
    os.utime(p, (mtime, mtime))
    return p


class TestSignalA:
    def test_burn_pattern_median_under_threshold(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(10)]
        result = signal_a_median_size(paths)
        assert result.match is True
        assert result.value < SIGNAL_A_MAX_BYTES

    def test_legitimate_pattern_median_over_threshold(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 200_000, 1000.0 + i * 60) for i in range(10)]
        result = signal_a_median_size(paths)
        assert result.match is False
        assert result.value > SIGNAL_A_MAX_BYTES

    def test_too_few_files_unobservable(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(3)]
        result = signal_a_median_size(paths)
        assert result.match is None
        assert result.unobservable is True


class TestSignalB:
    def test_burn_pattern_cadence_in_band_sustained(self, tmp_path):
        # 30 obs at 300s = 8700s span > 7200s
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(30)]
        result = signal_b_cadence_band(paths)
        assert result.match is True

    def test_legitimate_variable_cadence_not_band(self, tmp_path):
        import random
        random.seed(42)
        mtimes = [1000.0]
        for _ in range(29):
            mtimes.append(mtimes[-1] + random.uniform(60, 1200))
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 200_000, m) for i, m in enumerate(mtimes)]
        result = signal_b_cadence_band(paths)
        assert result.match is False

    def test_too_short_span_unobservable_or_false(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300) for i in range(5)]
        result = signal_b_cadence_band(paths)
        assert result.match is None or result.match is False


class TestSignalC:
    def test_burn_pattern_all_identical_prompts(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300, first_user_text="ok") for i in range(10)]
        result = signal_c_identical_prompts(paths)
        assert result.match is True
        assert result.value == "ok"

    def test_distinct_prompts_not_match(self, tmp_path):
        paths = [_make_jsonl(tmp_path, f"s{i}.jsonl", 200_000, 1000.0 + i * 300, first_user_text=f"prompt-{i}") for i in range(10)]
        result = signal_c_identical_prompts(paths)
        assert result.match is False

    def test_unreadable_majority_unobservable(self, tmp_path):
        good = [_make_jsonl(tmp_path, f"s{i}.jsonl", 54_000, 1000.0 + i * 300, first_user_text="ok") for i in range(4)]
        for i in range(4, 10):
            p = tmp_path / f"s{i}.jsonl"
            p.write_text(json.dumps({"type": "summary", "summary": "x"}) + "\n")
            os.utime(p, (1000.0 + i * 300, 1000.0 + i * 300))
        all_paths = good + [tmp_path / f"s{i}.jsonl" for i in range(4, 10)]
        result = signal_c_identical_prompts(all_paths)
        assert result.match is None
        assert result.unobservable is True
