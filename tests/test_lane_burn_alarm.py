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
    assert result == {"total": 1500, "nmsg": 2, "cache_read": 0}


def test_sum_usage_lines_empty_transcript_is_a_real_zero():
    result = a.sum_usage_lines([], datetime.now(timezone.utc) - timedelta(minutes=60))
    assert result == {"total": 0, "nmsg": 0, "cache_read": 0}


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
    assert result == {"total": 42, "nmsg": 1, "cache_read": 0}


def test_sum_usage_lines_tracks_cache_read_separately_from_total():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=60)
    lines = [_usage_line(now.isoformat(), input_t=100, output_t=0, cache_c=0, cache_r=900)]
    result = a.sum_usage_lines(lines, cutoff)
    assert result == {"total": 1000, "nmsg": 1, "cache_read": 900}


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


# --------------------------------------------------------------------------
# should_repage_breach (console #85152/#85266): 4h suppression, with escapes
# for a rate rise or a context-fill crossing -- not just a flat timer.
# --------------------------------------------------------------------------

def _kw(**over):
    kw = dict(suppress_min=240, rate_rise_frac=0.5, pct_escape=90)
    kw.update(over)
    return kw


def test_repage_true_when_never_paged_before():
    assert a.should_repage_breach(None, NOW, 50_000_000, 40, **_kw()) is True


def test_repage_true_on_legacy_bare_string_state():
    # state written before this field existed (a bare iso string, old _should_page shape)
    assert a.should_repage_breach(NOW.isoformat(), NOW, 50_000_000, 40, **_kw()) is True


def test_repage_false_within_suppress_window_same_rate_low_fill():
    prior = {"ts": (NOW - timedelta(hours=1)).isoformat(), "tph": 50_000_000}
    assert a.should_repage_breach(prior, NOW, 50_000_000, 40, **_kw()) is False


def test_repage_true_after_suppress_window_elapses():
    prior = {"ts": (NOW - timedelta(hours=5)).isoformat(), "tph": 50_000_000}
    assert a.should_repage_breach(prior, NOW, 50_000_000, 40, **_kw()) is True


def test_repage_true_on_rate_rise_escape_even_inside_suppress_window():
    prior = {"ts": (NOW - timedelta(minutes=30)).isoformat(), "tph": 50_000_000}
    # +50% or more vs the last PAGED rate -- escapes immediately
    assert a.should_repage_breach(prior, NOW, 75_000_000, 40, **_kw()) is True


def test_repage_false_on_small_rate_rise_under_the_escape_threshold():
    prior = {"ts": (NOW - timedelta(minutes=30)).isoformat(), "tph": 50_000_000}
    assert a.should_repage_breach(prior, NOW, 60_000_000, 40, **_kw()) is False  # +20%, not +50%


def test_repage_true_on_context_fill_escape_even_inside_suppress_window():
    prior = {"ts": (NOW - timedelta(minutes=30)).isoformat(), "tph": 50_000_000}
    assert a.should_repage_breach(prior, NOW, 50_000_000, 95, **_kw()) is True


def test_repage_false_when_context_pct_is_none_never_escapes():
    # gauge unavailable -- absence of data is not evidence of a crossed threshold
    prior = {"ts": (NOW - timedelta(minutes=30)).isoformat(), "tph": 50_000_000}
    assert a.should_repage_breach(prior, NOW, 50_000_000, None, **_kw()) is False


# --------------------------------------------------------------------------
# breach_alert_body (console #85266: add context fill % + cache-read share)
# --------------------------------------------------------------------------

def test_breach_alert_body_includes_context_fill_and_cache_read_share():
    b = {"agent_id": "cc-fleet-health", "total": 50_000_000, "nmsg": 12, "tph": 50_000_000.0,
         "cache_read_share": 0.82}
    body = a.breach_alert_body(b, window_min=60, threshold_tph=40_000_000, context_pct=67)
    assert "67%" in body
    assert "82%" in body
    assert "cc-fleet-health" in body


def test_breach_alert_body_reports_unknown_when_context_pct_missing():
    b = {"agent_id": "cc-x", "total": 1, "nmsg": 1, "tph": 50_000_000.0, "cache_read_share": None}
    body = a.breach_alert_body(b, window_min=60, threshold_tph=40_000_000, context_pct=None)
    assert "unknown" in body


# --------------------------------------------------------------------------
# _context_fill_map (enrichment only -- must never crash the alarm). Patches
# read_context_gauge on the REAL module object (not a sys.modules swap --
# `from scripts import context_health_watchdog` resolves via the `scripts`
# package's existing attribute, which a sys.modules-only swap would miss).
# --------------------------------------------------------------------------
import scripts.context_health_watchdog as _chw  # noqa: E402


def test_context_fill_map_builds_agent_to_pct_dict(monkeypatch):
    class _Row:
        def __init__(self, agent, pct):
            self.agent = agent
            self.pct = pct

    monkeypatch.setattr(_chw, "read_context_gauge",
                         lambda: [_Row("orch-console", 67), _Row("cc-fleet-health", 45)])
    assert a._context_fill_map() == {"orch-console": 67, "cc-fleet-health": 45}


def test_context_fill_map_never_raises_on_failure(monkeypatch):
    def boom():
        raise RuntimeError("gauge unreachable")

    monkeypatch.setattr(_chw, "read_context_gauge", boom)
    assert a._context_fill_map() == {}
