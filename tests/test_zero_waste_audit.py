import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_SPEC = importlib.util.spec_from_file_location(
    "zero_waste_audit",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "zero_waste_audit.py"),
)
zwa = importlib.util.module_from_spec(_SPEC)
sys.modules["zero_waste_audit"] = zwa
_SPEC.loader.exec_module(zwa)


def test_escape_path_replaces_slash_and_dot():
    assert zwa.escape_path("/Users/sheikhmusa/wingmen/projects/cosem-adcda.wt-split") == \
        "-Users-sheikhmusa-wingmen-projects-cosem-adcda-wt-split"


def test_hourly_buckets_sums_usage_within_window_only():
    now = datetime(2026, 10, 30, 6, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=2)
    lines = [
        json.dumps({"timestamp": "2026-10-30T05:30:00Z",
                    "message": {"usage": {"input_tokens": 10, "output_tokens": 5,
                                           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}),
        json.dumps({"timestamp": "2026-10-30T03:00:00Z",  # outside window
                    "message": {"usage": {"input_tokens": 999, "output_tokens": 999,
                                           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}),
        json.dumps({"timestamp": "2026-10-30T05:45:00Z",
                    "message": {"usage": {"input_tokens": 1, "output_tokens": 1,
                                           "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3}}}),
    ]
    buckets, nmsg, last_ts = zwa.hourly_buckets_from_lines(lines, start, now)
    assert nmsg == 2
    assert sum(buckets.values()) == 15 + 7
    assert last_ts == "2026-10-30T05:45:00+00:00"


def test_hourly_buckets_skips_malformed_lines_and_missing_usage():
    now = datetime(2026, 10, 30, 6, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=2)
    lines = [
        "not json",
        json.dumps({"no_timestamp": True}),
        json.dumps({"timestamp": "2026-10-30T05:30:00Z", "message": {"no_usage": True}}),
        "",
    ]
    buckets, nmsg, last_ts = zwa.hourly_buckets_from_lines(lines, start, now)
    assert buckets == {}
    assert nmsg == 0


def test_deepseek_dollar_buckets_reports_could_not_measure_below_two_points(tmp_path, monkeypatch):
    f = tmp_path / "deepseek_balance_history.jsonl"
    f.write_text(json.dumps({"ts": "2026-10-30T05:00:00Z", "balance_usd": 10.0}) + "\n")
    monkeypatch.setattr(zwa, "DEEPSEEK_BALANCE_FILE", f)
    now = datetime(2026, 10, 30, 6, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=1)
    result, err = zwa.deepseek_dollar_buckets(start, now)
    assert result is None
    assert "could not measure" in err


def test_deepseek_dollar_buckets_computes_spend_from_decreasing_balance(tmp_path, monkeypatch):
    f = tmp_path / "deepseek_balance_history.jsonl"
    f.write_text(
        json.dumps({"ts": "2026-10-30T04:00:00Z", "balance_usd": 10.0}) + "\n"
        + json.dumps({"ts": "2026-10-30T05:00:00Z", "balance_usd": 9.5}) + "\n"
    )
    monkeypatch.setattr(zwa, "DEEPSEEK_BALANCE_FILE", f)
    now = datetime(2026, 10, 30, 6, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=3)
    result, err = zwa.deepseek_dollar_buckets(start, now)
    assert err is None
    assert sum(result["buckets"].values()) == 0.5


def test_deepseek_dollar_buckets_never_counts_a_balance_increase_as_spend(tmp_path, monkeypatch):
    f = tmp_path / "deepseek_balance_history.jsonl"
    f.write_text(
        json.dumps({"ts": "2026-10-30T04:00:00Z", "balance_usd": 9.0}) + "\n"
        + json.dumps({"ts": "2026-10-30T05:00:00Z", "balance_usd": 50.0}) + "\n"  # top-up
    )
    monkeypatch.setattr(zwa, "DEEPSEEK_BALANCE_FILE", f)
    now = datetime(2026, 10, 30, 6, 0, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=3)
    result, err = zwa.deepseek_dollar_buckets(start, now)
    assert err is None
    assert sum(result["buckets"].values()) == 0.0
