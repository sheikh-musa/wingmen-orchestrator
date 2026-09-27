"""context_truth: fail-loud on a mis-called gauge, + a by-agent convenience (bus #44344/#44349).

A reviewer called `lane_fire_reading('cosem-port')` — passing the LANE NAME where an int
token count belongs — and got back a plausible "UNKNOWN — gauge is unreadable (bad data:
cosem-port tokens vs 1000000 window); PAGE this lane". That authoritative-sounding verdict
made a careful operator believe the 1M-window gauge was broken (it was not; the lane was
genuinely at 97%). A canonical reader must NOT dress up a caller's type error as a gauge
reading — it must fail loud. And the call the reviewer reached for (by agent/lane) should exist.
"""
import importlib.util
import pathlib

import pytest

_MOD = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "context_truth.py"
_spec = importlib.util.spec_from_file_location("context_truth", _MOD)
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)


# ---- 1) fail-loud guard on a non-numeric gauge_tokens (the mis-call) ----
def test_lane_fire_reading_raises_on_string_gauge_tokens():
    with pytest.raises(TypeError) as ei:
        ct.lane_fire_reading("cosem-port")            # the exact mis-call from #44344
    assert "gauge_tokens" in str(ei.value)
    assert "cosem-port" in str(ei.value)              # names the offending value
    assert "lane_fire_reading_for_agent" in str(ei.value)  # points at the right call


def test_resolve_raises_on_string_gauge_tokens():
    with pytest.raises(TypeError):
        ct.resolve(gauge_tokens="cosem-port")


def test_numeric_and_none_gauge_tokens_still_work():
    # None and real ints must be unaffected by the guard.
    assert ct.lane_fire_reading(gauge_tokens=None).known is False           # no gauge → unknown
    r = ct.lane_fire_reading(gauge_tokens=93512, gauge_age_s=186, window=1_000_000)
    assert r.known is True and r.pct == 9 and r.level == "green"
    r2 = ct.lane_fire_reading(gauge_tokens=965163, gauge_age_s=175, window=1_000_000)
    assert r2.known is True and r2.pct == 97 and r2.level == "red"


# ---- 2) lane_fire_reading_for_agent: fetch the gauge, then read canonically ----
def test_for_agent_reads_fetched_gauge_known():
    r = ct.lane_fire_reading_for_agent("cc-cosem-platform",
                                       _fetch=lambda ident: (93512, 186))
    assert r.known is True and r.pct == 9 and r.level == "green" and r.source == "gauge"


def test_for_agent_high_gauge_is_red():
    r = ct.lane_fire_reading_for_agent("cosem-port", _fetch=lambda ident: (965163, 175))
    assert r.known is True and r.pct == 97 and r.level == "red"


def test_for_agent_no_gauge_row_is_unknown_not_crash():
    r = ct.lane_fire_reading_for_agent("nobody", _fetch=lambda ident: (None, None))
    assert r.known is False and r.pct is None
    assert "no gauge reading available" in r.reason


def test_for_agent_stale_gauge_is_unknown():
    r = ct.lane_fire_reading_for_agent("cc-cosem-platform",
                                       _fetch=lambda ident: (800000, 99999),  # very stale
                                       max_gauge_age_s=1800)
    assert r.known is False and "stale" in r.reason


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
