from datetime import datetime, timedelta, timezone

import pytest

from scripts import gzb_monitor as gm

T0 = datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc)

REAL_PROBE_OUTPUT = (
    "3.00 1.97 1.64 3/417 3432022\n"
    "GZBMON_SEP"
    "\n               total        used        free      shared  buff/cache   available\n"
    "Mem:           31004        4831        8546         457       18539       26173\n"
    "Swap:           8191        3205        4986\n"
    "GZBMON_SEP"
    "\nFilesystem                        1K-blocks     Used Available Use% Mounted on\n"
    "/dev/mapper/ubuntu--vg-ubuntu--lv 956638200 93402684 823250568  11% /\n"
    "GZBMON_SEP"
    "\n4\n"
    "GZBMON_SEP"
    "\n61000\n"
    "GZBMON_SEP"
    "\n8\n"
)


def test_parse_probe_output_real_sample():
    out = gm.parse_probe_output(REAL_PROBE_OUTPUT)
    assert out["loadavg_1m"] == 3.00
    assert out["cpu_count"] == 4
    assert out["mem_total_mb"] == 31004
    assert out["mem_real_used_mb"] == 31004 - 26173
    assert out["mem_real_used_pct"] == pytest.approx(15.6, abs=0.1)
    assert out["swap_total_mb"] == 8191
    assert out["swap_used_mb"] == 3205
    assert out["disk_total_gb"] == pytest.approx(912.3, abs=0.1)
    assert out["cpu_temp_c"] == 61.0
    assert out["cpu_temp_source"] == "thermal_zone0"
    assert out["claude_sessions"] == 8


def test_parse_probe_output_thermal_zone_absent_is_could_not_measure():
    text = REAL_PROBE_OUTPUT.replace("\n61000\n", "\nNA\n")
    out = gm.parse_probe_output(text)
    assert out["cpu_temp_c"] is None
    assert out["cpu_temp_source"] == "unavailable"


def test_parse_probe_output_wrong_section_count_raises():
    with pytest.raises(gm.ProbeParseError):
        gm.parse_probe_output("not the expected shape at all")


def test_parse_probe_output_real_used_excludes_buff_cache():
    out = gm.parse_probe_output(REAL_PROBE_OUTPUT)
    # naive "used" column (4831) is close here, but real_used is driven by
    # `available`, NOT by subtracting buff_cache from total blindly
    assert out["mem_real_used_mb"] != out["mem_total_mb"] - out["mem_buff_cache_mb"]


# ---------------------------------------------------------------- alerts

def _sample(minutes_ago, ok=True, mem_pct=None, disk_pct=None, temp_c=None):
    ts = T0 - timedelta(minutes=minutes_ago)
    metrics = {}
    if mem_pct is not None:
        metrics["mem_real_used_pct"] = mem_pct
    if disk_pct is not None:
        metrics["disk_free_pct"] = disk_pct
    if temp_c is not None:
        metrics["cpu_temp_c"] = temp_c
    return {"ts": ts, "ok": ok, "metrics": metrics, "error": None}


def test_mem_alert_sustained():
    samples = [_sample(m, mem_pct=95.0) for m in (15, 10, 5, 0)]
    assert [f["kind"] for f in gm.evaluate_alerts(samples, T0)] == ["gzb_mem"]


def test_disk_alert_latest_sample():
    samples = [_sample(0, disk_pct=5.0)]
    assert [f["kind"] for f in gm.evaluate_alerts(samples, T0)] == ["gzb_disk"]


def test_temp_alert_sustained():
    samples = [_sample(m, temp_c=90.0) for m in (10, 5, 0)]
    assert [f["kind"] for f in gm.evaluate_alerts(samples, T0)] == ["gzb_temp"]


def test_no_alerts_when_healthy():
    samples = [_sample(m, mem_pct=20.0, disk_pct=80.0, temp_c=50.0) for m in (15, 10, 5, 0)]
    assert gm.evaluate_alerts(samples, T0) == []


# ---------------------------------------------------------------- collect() boundary

class _FakeCP:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_collect_reports_unreachable_on_ssh_failure(monkeypatch):
    monkeypatch.setattr(gm, "_ssh_run", lambda cmd, timeout: _FakeCP(returncode=255, stderr="Connection refused"))
    ok, metrics, error = gm.collect()
    assert ok is False
    assert metrics["reachable"] is False
    assert "unreachable" in error


def test_collect_reports_parse_failure_as_reachable_but_not_ok(monkeypatch):
    monkeypatch.setattr(gm, "_ssh_run", lambda cmd, timeout: _FakeCP(returncode=0, stdout="garbage"))
    ok, metrics, error = gm.collect()
    assert ok is False
    assert metrics["reachable"] is True
    assert "unparseable" in error


def test_collect_succeeds_on_real_shaped_output(monkeypatch):
    monkeypatch.setattr(gm, "_ssh_run", lambda cmd, timeout: _FakeCP(returncode=0, stdout=REAL_PROBE_OUTPUT))
    ok, metrics, error = gm.collect()
    assert ok is True
    assert metrics["claude_sessions"] == 8
