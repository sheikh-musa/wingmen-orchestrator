from datetime import datetime, timezone

import pytest

from scripts import cubeasht_job_memory_report as rpt

T0 = datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc)


def _sample(minutes_ago, ok=True, wsl_pct=None, wsl_mb=None, busy=None):
    from datetime import timedelta
    ts = T0 - timedelta(minutes=minutes_ago)
    metrics = {}
    if wsl_pct is not None or wsl_mb is not None:
        metrics["wsl"] = {}
        if wsl_pct is not None:
            metrics["wsl"]["mem_used_pct"] = wsl_pct
        if wsl_mb is not None:
            metrics["wsl"]["mem_used_mb"] = wsl_mb
    if busy is not None:
        metrics["runner"] = {"busy": busy}
    return {"ts": ts, "ok": ok, "metrics": metrics, "error": None}


def _job(name, started, completed, runner="cubeasht-orchestrator", run_id=1, job_id=None):
    return {"name": name, "started_at": started, "completed_at": completed,
            "runner_name": runner, "run_id": run_id, "id": job_id or started}


# ---------------------------------------------------------------- pure helpers

def test_wsl_used_mb_and_pct():
    s = _sample(0, wsl_pct=42.0, wsl_mb=1000.0)
    assert rpt.wsl_used_mb(s) == 1000.0
    assert rpt.wsl_used_pct(s) == 42.0
    assert rpt.wsl_used_mb(_sample(0, ok=False, wsl_mb=1000.0)) is None
    assert rpt.wsl_used_mb(_sample(0)) is None


def test_sample_is_busy():
    assert rpt.sample_is_busy(_sample(0, busy=True)) is True
    assert rpt.sample_is_busy(_sample(0, busy=False)) is False
    assert rpt.sample_is_busy(_sample(0)) is False
    assert rpt.sample_is_busy(_sample(0, ok=False, busy=True)) is False


def test_percentile_nearest_rank():
    assert rpt.percentile([1, 2, 3, 4, 5], 50) == 3
    assert rpt.percentile([1, 2, 3, 4, 5], 95) == 5
    assert rpt.percentile([10], 95) == 10


def test_job_duration_min():
    j = _job("x", "2026-10-30T00:00:00Z", "2026-10-30T00:10:00Z")
    assert rpt.job_duration_min(j) == pytest.approx(10.0)
    assert rpt.job_duration_min(_job("x", "2026-10-30T00:00:00Z", None)) is None
    assert rpt.job_duration_min(_job("x", None, "2026-10-30T00:00:00Z")) is None


def test_job_window_ongoing_job_ends_at_now():
    now = rpt._parse_ts("2026-10-30T00:20:00Z")
    j = _job("x", "2026-10-30T00:00:00Z", None)
    start, end = rpt.job_window(j, now)
    assert start == rpt._parse_ts("2026-10-30T00:00:00Z")
    assert end == now


def test_find_overlaps_detects_concurrent_jobs_across_runners():
    a = _job("A", "2026-10-30T00:00:00Z", "2026-10-30T00:10:00Z", runner="cubeasht-orchestrator", job_id=1)
    b = _job("B", "2026-10-30T00:05:00Z", "2026-10-30T00:15:00Z", runner="cubeasht-orchestrator-2", job_id=2)
    now = rpt._parse_ts("2026-10-30T00:20:00Z")
    pairs = rpt.find_overlaps([a, b], now)
    assert len(pairs) == 1


def test_find_overlaps_sequential_jobs_do_not_overlap():
    a = _job("A", "2026-10-30T00:00:00Z", "2026-10-30T00:10:00Z", job_id=1)
    b = _job("B", "2026-10-30T00:10:00Z", "2026-10-30T00:20:00Z", job_id=2)
    now = rpt._parse_ts("2026-10-30T00:20:00Z")
    assert rpt.find_overlaps([a, b], now) == []


def test_job_containing():
    a = _job("A", "2026-10-30T00:00:00Z", "2026-10-30T00:10:00Z")
    b = _job("B", "2026-10-30T00:30:00Z", "2026-10-30T00:40:00Z")
    now = rpt._parse_ts("2026-10-30T01:00:00Z")
    inside = rpt.job_containing([a, b], rpt._parse_ts("2026-10-30T00:05:00Z"), now)
    assert [j["name"] for j in inside] == ["A"]
    outside = rpt.job_containing([a, b], rpt._parse_ts("2026-10-30T00:20:00Z"), now)
    assert outside == []


# ---------------------------------------------------------------- build_report (mocked boundaries)

class _FakeMon:
    HOST = "cubeasht"

    def __init__(self, samples=None, raise_on_connect=False):
        self._samples = samples or []
        self._raise = raise_on_connect

    def connect(self):
        if self._raise:
            raise RuntimeError("simulated DB connect failure")
        return object()

    def fetch_samples(self, conn, host, since, until):
        return self._samples


def test_build_report_memory_fails_loud_on_db_error(monkeypatch):
    monkeypatch.setattr(rpt, "mon", _FakeMon(raise_on_connect=True))
    monkeypatch.setattr(rpt, "fetch_cubeasht_runner_names", lambda: ["cubeasht-orchestrator"])
    monkeypatch.setattr(rpt, "fetch_jobs_overlapping_window", lambda *a, **k: [])
    out = rpt.build_report(T0, T0)
    assert "could not measure" in out["memory"]["error"]


def test_build_report_memory_fails_loud_on_zero_busy_samples(monkeypatch):
    samples = [_sample(10, wsl_pct=20.0, wsl_mb=500.0, busy=False)]
    monkeypatch.setattr(rpt, "mon", _FakeMon(samples=samples))
    monkeypatch.setattr(rpt, "fetch_cubeasht_runner_names", lambda: ["cubeasht-orchestrator"])
    monkeypatch.setattr(rpt, "fetch_jobs_overlapping_window", lambda *a, **k: [])
    out = rpt.build_report(T0, T0)
    assert out["memory"]["overall_peak_mb"] == 500.0
    assert "could not measure" in out["memory"]["busy_peak_mb"]


def test_build_report_memory_peak_and_p95_during_busy(monkeypatch):
    samples = [
        _sample(30, wsl_pct=10.0, wsl_mb=300.0, busy=False),
        _sample(20, wsl_pct=50.0, wsl_mb=4000.0, busy=True),
        _sample(10, wsl_pct=70.0, wsl_mb=6000.0, busy=True),
        _sample(0, wsl_pct=60.0, wsl_mb=5000.0, busy=True),
    ]
    monkeypatch.setattr(rpt, "mon", _FakeMon(samples=samples))
    monkeypatch.setattr(rpt, "fetch_cubeasht_runner_names", lambda: ["cubeasht-orchestrator"])
    monkeypatch.setattr(rpt, "fetch_jobs_overlapping_window", lambda *a, **k: [])
    out = rpt.build_report(T0, T0)
    assert out["memory"]["overall_peak_mb"] == 6000.0
    assert out["memory"]["busy_peak_mb"] == 6000.0
    assert out["memory"]["samples_busy"] == 3


def test_build_report_jobs_fails_loud_on_gh_error(monkeypatch):
    monkeypatch.setattr(rpt, "mon", _FakeMon(samples=[]))
    def _boom():
        raise rpt.ReportError("simulated gh failure")
    monkeypatch.setattr(rpt, "fetch_cubeasht_runner_names", _boom)
    out = rpt.build_report(T0, T0)
    assert "could not measure" in out["jobs"]["error"]


def test_build_report_longest_job_and_overlap_flag(monkeypatch):
    a = _job("short", "2026-10-30T00:00:00Z", "2026-10-30T00:05:00Z", job_id=1)
    b = _job("long", "2026-10-30T00:10:00Z", "2026-10-30T00:40:00Z", runner="cubeasht-orchestrator-2", job_id=2)
    monkeypatch.setattr(rpt, "mon", _FakeMon(samples=[]))
    monkeypatch.setattr(rpt, "fetch_cubeasht_runner_names", lambda: ["cubeasht-orchestrator", "cubeasht-orchestrator-2"])
    monkeypatch.setattr(rpt, "fetch_jobs_overlapping_window", lambda *args, **kwargs: [a, b])
    out = rpt.build_report(T0, T0)
    assert out["jobs"]["longest_job_by_duration"]["name"] == "long"
    assert out["jobs"]["jobs_ever_overlapped"] is False


def test_build_findings_notes_overlap_when_present():
    out = {"jobs": {"runner_names_scoped": ["cubeasht-orchestrator", "cubeasht-orchestrator-2"],
                     "jobs_overlapping_window": 5, "jobs_ever_overlapped": True}}
    findings = rpt.build_findings(out)
    assert len(findings) == 1
    assert "overlapped" in findings[0]


def test_build_findings_notes_no_overlap_caveat_when_absent():
    out = {"jobs": {"runner_names_scoped": ["cubeasht-orchestrator", "cubeasht-orchestrator-2"],
                     "jobs_overlapping_window": 5, "jobs_ever_overlapped": False}}
    findings = rpt.build_findings(out)
    assert "UNDERSTATE" in findings[0]


def test_build_findings_empty_when_jobs_errored():
    assert rpt.build_findings({"jobs": {"error": "could not measure"}}) == []


def test_gh_api_paginated_stops_on_short_page(monkeypatch):
    calls = []

    class _CP:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, **kwargs):
        path = cmd[-1]
        calls.append(path)
        if "page=1" in path:
            import json as _j
            return _CP(_j.dumps({"things": [{"a": 1}] * rpt.PER_PAGE}))
        return _CP('{"things": [{"a": 2}]}')

    monkeypatch.setattr(rpt.subprocess, "run", fake_run)
    out = rpt.gh_api_paginated("repos/x/y/actions/runs", "things")
    assert len(out) == rpt.PER_PAGE + 1
    assert len(calls) == 2


def test_gh_api_paginated_raises_on_gh_failure(monkeypatch):
    class _CP:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(rpt.subprocess, "run", lambda *a, **k: _CP())
    with pytest.raises(rpt.ReportError):
        rpt.gh_api_paginated("repos/x/y/actions/runs", "things")
