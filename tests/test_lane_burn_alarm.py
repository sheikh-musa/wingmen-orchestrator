"""lane_burn_alarm.py (op#35929/35930) -- per-lane tokens/hour burn alarm.

Pure-helper tests (escape_cwd, sum_usage_lines, tokens_per_hour, evaluate_lane)
need no DB/subprocess. The orchestration tests (measure_lane) inject fakes for
the tmux/ssh/filesystem boundary so no real tmux session or ssh host is
required -- the point under test is "a failure at any of those boundaries
reports 'error' with a reason, never a fabricated number."
"""
import json
from datetime import datetime, timedelta, timezone

import scripts.lane_burn_alarm as a


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def test_escape_cwd_replaces_slash_and_dot():
    assert a.escape_cwd("/Users/sheikhmusa/wingmen/projects/cosem-adcda.wt-split") == \
        "-Users-sheikhmusa-wingmen-projects-cosem-adcda-wt-split"


def test_pick_latest_jsonl_picks_max_mtime():
    files = ["a.jsonl", "b.jsonl", "c.jsonl"]
    mtimes = {"a.jsonl": 10.0, "b.jsonl": 30.0, "c.jsonl": 20.0}
    assert a.pick_latest_jsonl(files, mtimes) == "b.jsonl"


def test_pick_latest_jsonl_empty_list_is_none():
    assert a.pick_latest_jsonl([], {}) is None


def _usage_line(ts, input_t=100, output_t=50, cache_c=0, cache_r=0):
    return json.dumps({
        "timestamp": ts,
        "message": {"usage": {
            "input_tokens": input_t, "output_tokens": output_t,
            "cache_creation_input_tokens": cache_c, "cache_read_input_tokens": cache_r,
        }},
    })


def test_sum_usage_lines_includes_only_within_window():
    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = now - timedelta(minutes=60)
    lines = [
        _usage_line((now - timedelta(minutes=90)).isoformat()),  # outside window
        _usage_line((now - timedelta(minutes=30)).isoformat(), input_t=1000, output_t=0),  # inside
        _usage_line((now - timedelta(minutes=5)).isoformat(), input_t=0, output_t=500),  # inside
    ]
    result = a.sum_usage_lines(lines, cutoff)
    assert result == {"total": 1500, "nmsg": 2}


def test_sum_usage_lines_empty_transcript_is_a_real_zero():
    result = a.sum_usage_lines([], datetime.now(timezone.utc) - timedelta(minutes=60))
    assert result == {"total": 0, "nmsg": 0}


def test_sum_usage_lines_skips_malformed_and_usageless_lines():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=60)
    lines = [
        "not json at all {{{",
        json.dumps({"timestamp": now.isoformat(), "message": {}}),  # no usage
        json.dumps({"no_timestamp": True}),
        _usage_line(now.isoformat(), input_t=42, output_t=0),
    ]
    result = a.sum_usage_lines(lines, cutoff)
    assert result == {"total": 42, "nmsg": 1}


def test_tokens_per_hour_scales_by_window():
    assert a.tokens_per_hour(1_000_000, 60) == 1_000_000.0
    assert a.tokens_per_hour(1_000_000, 30) == 2_000_000.0
    assert a.tokens_per_hour(0, 60) == 0.0


def test_tokens_per_hour_guards_zero_window():
    assert a.tokens_per_hour(1_000_000, 0) == 0.0


def test_evaluate_lane_breach_ok_unmeasured():
    assert a.evaluate_lane({"tph": 20_000_000}, threshold_tph=15_000_000) == "breach"
    assert a.evaluate_lane({"tph": 1_000_000}, threshold_tph=15_000_000) == "ok"
    assert a.evaluate_lane({"tph": 15_000_000}, threshold_tph=15_000_000) == "breach"  # >= threshold
    assert a.evaluate_lane(None, threshold_tph=15_000_000) == "unmeasured"


# --------------------------------------------------------------------------
# measure_lane orchestration -- boundaries injected via monkeypatch
# --------------------------------------------------------------------------

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_measure_lane_local_happy_path(monkeypatch):
    lane = {"agent_id": "cc-finance-1", "host": "Sheikhs-Mini", "tmux_session": "finance"}
    monkeypatch.setattr(a, "_list_panes_local", lambda: {"finance": "/Users/x/wingmen/projects/finance"})
    monkeypatch.setattr(a, "_read_transcript_lines_local", lambda cwd: [
        _usage_line((NOW - timedelta(minutes=10)).isoformat(), input_t=600_000, output_t=0),
    ])
    result = a.measure_lane(lane, window_min=60, now=NOW)
    assert result["status"] == "ok"
    assert result["total"] == 600_000
    assert result["tph"] == 600_000.0


def test_measure_lane_remote_host_uses_ssh_paths(monkeypatch):
    lane = {"agent_id": "cc-irsyad-1", "host": "gzbai", "tmux_session": "irsyad-worker-1"}
    monkeypatch.setattr(a, "_list_panes_remote", lambda alias: {"irsyad-worker-1": "/home/gazzai/wingmen/projects/ihsanos-irsyad"})
    monkeypatch.setattr(a, "_read_transcript_lines_remote", lambda alias, cwd: [
        _usage_line((NOW - timedelta(minutes=5)).isoformat(), input_t=10_000, output_t=10_000),
    ])
    result = a.measure_lane(lane, window_min=60, now=NOW)
    assert result["status"] == "ok"
    assert result["total"] == 20_000


def test_measure_lane_no_pane_is_error_not_zero(monkeypatch):
    lane = {"agent_id": "cc-ghost-1", "host": "Sheikhs-Mini", "tmux_session": "ghost-session"}
    monkeypatch.setattr(a, "_list_panes_local", lambda: {"some-other-session": "/x"})
    result = a.measure_lane(lane, window_min=60, now=NOW)
    assert result["status"] == "error"
    assert "ghost-session" in result["reason"]
    assert "tph" not in result


def test_measure_lane_ssh_failure_is_error_not_zero(monkeypatch):
    lane = {"agent_id": "cc-irsyad-1", "host": "gzbai", "tmux_session": "irsyad-worker-1"}
    def boom(alias):
        raise RuntimeError("ssh gzb tmux list-panes failed: Connection timed out")
    monkeypatch.setattr(a, "_list_panes_remote", boom)
    result = a.measure_lane(lane, window_min=60, now=NOW)
    assert result["status"] == "error"
    assert "timed out" in result["reason"]
    assert "tph" not in result


def test_measure_lane_missing_transcript_is_error_not_zero(monkeypatch):
    lane = {"agent_id": "cc-finance-1", "host": "Sheikhs-Mini", "tmux_session": "finance"}
    monkeypatch.setattr(a, "_list_panes_local", lambda: {"finance": "/Users/x/wingmen/projects/finance"})
    def boom(cwd):
        raise FileNotFoundError("no project dir")
    monkeypatch.setattr(a, "_read_transcript_lines_local", boom)
    result = a.measure_lane(lane, window_min=60, now=NOW)
    assert result["status"] == "error"
    assert "no project dir" in result["reason"]


# --------------------------------------------------------------------------
# Dedup state (_should_page)
# --------------------------------------------------------------------------

def test_should_page_true_when_no_prior_state():
    assert a._should_page({}, "breach:cc-x", NOW, repage_min=60) is True


def test_should_page_false_within_backoff_window():
    state = {"breach:cc-x": (NOW - timedelta(minutes=10)).isoformat()}
    assert a._should_page(state, "breach:cc-x", NOW, repage_min=60) is False


def test_should_page_true_after_backoff_window_elapses():
    state = {"breach:cc-x": (NOW - timedelta(minutes=90)).isoformat()}
    assert a._should_page(state, "breach:cc-x", NOW, repage_min=60) is True


def test_should_page_true_on_corrupt_state_value():
    state = {"breach:cc-x": "not-a-timestamp"}
    assert a._should_page(state, "breach:cc-x", NOW, repage_min=60) is True
