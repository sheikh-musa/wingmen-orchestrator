from datetime import datetime, timedelta, timezone

import pytest

from scripts import mini_monitor as mm

T0 = datetime(2026, 10, 30, 0, 0, tzinfo=timezone.utc)

VM_STAT_SAMPLE = """Mach Virtual Memory Statistics: (page size of 4096 bytes)
Pages free:                               36749.
Pages active:                            800000.
Pages inactive:                          700000.
Pages speculative:                        28288.
Pages throttled:                              0.
Pages wired down:                        300000.
Pages purgeable:                           2663.
Pages occupied by compressor:            100000.
"""


def test_parse_vm_stat_extracts_expected_fields():
    pages = mm.parse_vm_stat(VM_STAT_SAMPLE)
    assert pages["free"] == 36749
    assert pages["active"] == 800000
    assert pages["inactive"] == 700000
    assert pages["wired"] == 300000
    assert pages["compressed"] == 100000


def test_parse_vm_stat_raises_on_missing_fields():
    with pytest.raises(mm.ProbeParseError):
        mm.parse_vm_stat("garbage output with no colons or fields")


def test_compute_memory_excludes_inactive_and_free_from_real_used():
    pages = {"active": 800000, "wired": 300000, "compressed": 100000, "inactive": 700000, "free": 36749}
    page_size = 4096
    mem_total_bytes = (800000 + 300000 + 100000 + 700000 + 36749) * page_size
    out = mm.compute_memory(pages, page_size, mem_total_bytes)
    real_used_pages = 800000 + 300000 + 100000  # inactive and free excluded
    expected_pct = round(real_used_pages / (800000 + 300000 + 100000 + 700000 + 36749) * 100, 1)
    assert out["mem_real_used_pct"] == pytest.approx(expected_pct, abs=0.1)
    # naive total-minus-free would be much higher since it counts inactive as used
    naive_pct = round((mem_total_bytes - 36749 * page_size) / mem_total_bytes * 100, 1)
    assert out["mem_real_used_pct"] < naive_pct


def test_parse_swapusage_normal_line():
    text = "vm.swapusage: total = 3072.00M  used = 2140.00M  free = 932.00M  (encrypted)"
    out = mm.parse_swapusage(text)
    assert out["swap_total_mb"] == 3072.0
    assert out["swap_used_mb"] == 2140.0
    assert out["swap_used_pct"] == pytest.approx(69.66, abs=0.1)


def test_parse_swapusage_raises_on_unparseable():
    with pytest.raises(mm.ProbeParseError):
        mm.parse_swapusage("not the expected format at all")


def test_parse_therm_no_recorded_fields_means_not_throttled():
    text = ("Note: No thermal warning level has been recorded\n"
            "Note: No performance warning level has been recorded\n")
    out = mm.parse_therm(text)
    assert out["throttled"] is False


def test_parse_therm_detects_a_recorded_limit_below_100():
    text = "CPU_Scheduler_Limit\t= 50\nCPU_Speed_Limit\t= 50\n"
    out = mm.parse_therm(text)
    assert out["throttled"] is True


def test_parse_therm_limit_at_100_is_not_throttled():
    text = "CPU_Scheduler_Limit\t= 100\n"
    out = mm.parse_therm(text)
    assert out["throttled"] is False


# ---------------------------------------------------------------- alerts

def _sample(minutes_ago, ok=True, mem_pct=None, disk_pct=None):
    ts = T0 - timedelta(minutes=minutes_ago)
    metrics = {}
    if mem_pct is not None:
        metrics["mem_real_used_pct"] = mem_pct
    if disk_pct is not None:
        metrics["disk_free_pct"] = disk_pct
    return {"ts": ts, "ok": ok, "metrics": metrics, "error": None}


def test_mem_alert_sustained_15_min():
    samples = [_sample(m, mem_pct=95.0) for m in (15, 10, 5, 0)]
    findings = mm.evaluate_alerts(samples, T0)
    assert [f["kind"] for f in findings] == ["mini_mem"]


def test_mem_alert_not_sustained_long_enough():
    samples = [_sample(m, mem_pct=95.0) for m in (10, 5, 0)]
    assert mm.evaluate_alerts(samples, T0) == []


def test_disk_alert_on_latest_sample():
    samples = [_sample(0, disk_pct=5.0)]
    findings = mm.evaluate_alerts(samples, T0)
    assert [f["kind"] for f in findings] == ["mini_disk"]


def test_no_alerts_when_healthy():
    samples = [_sample(m, mem_pct=50.0, disk_pct=50.0) for m in (15, 10, 5, 0)]
    assert mm.evaluate_alerts(samples, T0) == []


def test_render_page_mem_and_disk():
    subj, body = mm.render_page({"kind": "mini_mem", "latest": 95.0, "peak": 96.0,
                                  "threshold": 90.0, "window_min": 15})
    assert "memory" in subj.lower()
    assert "95" in subj
    subj2, body2 = mm.render_page({"kind": "mini_disk", "free_pct": 5.0, "free_gb": 10.0,
                                    "threshold": 15.0})
    assert "disk" in subj2.lower()


# ---------------------------------------------------------------- collect() boundary

def test_collect_survives_individual_probe_failures(monkeypatch):
    import os

    class _CP:
        def __init__(self, stdout="", returncode=0):
            self.stdout, self.returncode, self.stderr = stdout, returncode, ""

    def fake_run(args, timeout):
        if args[0] == "vm_stat":
            raise FileNotFoundError("no such binary")
        if args[:2] == ["sysctl", "-n"] and args[2] == "hw.ncpu":
            return _CP("4\n")
        return _CP("")

    monkeypatch.setattr(mm, "_run", fake_run)
    monkeypatch.setattr(os, "getloadavg", lambda: (1.0, 2.0, 3.0))
    ok, metrics, error = mm.collect()
    assert "memory" in metrics.get("probe_errors", {})
    assert ok is True  # other probes still succeeded
