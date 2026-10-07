"""Tests for scripts/cubeasht_monitor.py — parsers, alert windows, dedup, retention, summary.

Pure-function tests need no network. The migration/retention tests use the
ephemeral local pg17 `pg_dsn` fixture (never prod).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import cubeasht_monitor as cm  # noqa: E402

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

# Real output captured read-only from cubeasht on 2026-10-07 (LHM absent).
PS_REAL = (
    "CUBEMON_JSON_BEGIN\r\n"
    '{"ram_total_kb":32657000,"ram_free_kb":16294836,"last_boot":"2026-10-02T05:50:28.5000000Z",'
    '"cpu_name":"AMD Ryzen 5 7600X 6-Core Processor             ","cpu_load_pct":4,"cpu_threads":12,'
    '"disk_c_used_bytes":501028982784,"disk_c_free_bytes":1498331766784,'
    '"nvidia_smi":"NVIDIA GeForce RTX 4060, 52, 37, 2909, 8188",'
    '"disks":[{"name":"SHPP41-2000GM","bus":"NVMe","media":"SSD","temp_c":58,"temp_max_c":86,"wear_pct":0}],'
    '"lhm_error":"Invalid namespace ","vmmem_ws_bytes":2599096320}\r\n'
    "CUBEMON_JSON_END\r\n"
)

WSL_REAL = """CUBEMON_WSL_BEGIN
runner_service=active
uptime_s=11496.59
loadavg=0.06 0.16 0.17
FREE_BEGIN
               total        used        free      shared  buff/cache   available
Mem:           15558        1328       12607         223        2086       14229
Swap:           4096           0        4096
FREE_END
CUBEMON_WSL_END
"""

RUNNER = {"name": "cubeasht-orchestrator", "status": "online", "busy": False}


# ---------------------------------------------------------------- parsers

def test_parse_real_ps_output_lhm_absent():
    ps = cm.parse_ps_output(PS_REAL)
    m = cm.build_metrics(ps, cm.parse_wsl_output(WSL_REAL), RUNNER)
    assert m["reachable"] is True
    assert m["cpu_load_pct"] == 4.0 and m["cpu_threads"] == 12
    assert m["cpu_name"] == "AMD Ryzen 5 7600X 6-Core Processor"
    assert m["cpu_temp_c"] is None and m["cpu_temp_source"] == "unavailable"
    assert m["gpu_temp_c"] == 52.0 and m["gpu_util_pct"] == 37.0
    assert m["gpus"][0]["name"] == "NVIDIA GeForce RTX 4060"
    assert m["nvme_temp_c"] == 58.0 and m["disks"][0]["temp_max_c"] == 86.0
    assert m["ram_used_pct"] == pytest.approx(50.1, abs=0.1)
    assert m["disk_c_free_pct"] == pytest.approx(74.9, abs=0.1)
    assert m["vmmem_ws_mb"] == 2479
    assert m["wsl"]["runner_service"] == "active"
    assert m["probe_errors"]["lhm"].startswith("Invalid namespace")


def test_parse_ps_strips_nul_and_rejects_garbage():
    nul = "\0".join(PS_REAL)  # UTF-16-ish NUL-interleaved output
    assert cm.parse_ps_output(nul)["cpu_threads"] == 12
    with pytest.raises(cm.ProbeParseError):
        cm.parse_ps_output("'powershell' is not recognized as an internal or external command")
    with pytest.raises(cm.ProbeParseError):
        cm.parse_ps_output("CUBEMON_JSON_BEGIN\n{not json\nCUBEMON_JSON_END")


def test_lhm_present_cpu_package_preferred():
    ps = cm.parse_ps_output(PS_REAL)
    ps.pop("lhm_error")
    ps["lhm_cpu_temps"] = [{"name": "Core (Tctl/Tdie)", "value": 71.5},
                           {"name": "CPU Package", "value": 69.25}]
    assert cm.extract_cpu_temp(ps) == (69.25, "librehardwaremonitor")
    # single sensor rendered by ConvertTo-Json as a bare object, Tctl only
    ps["lhm_cpu_temps"] = {"name": "Core (Tctl/Tdie)", "value": 64}
    assert cm.extract_cpu_temp(ps) == (64.0, "librehardwaremonitor")
    # namespace exists but no CPU temperature sensor
    ps["lhm_cpu_temps"] = []
    assert cm.extract_cpu_temp(ps) == (None, "unavailable")


def test_missing_gpu_and_missing_nvme_counters():
    ps = cm.parse_ps_output(PS_REAL)
    ps.pop("nvidia_smi")
    ps["gpu_error"] = "The term 'nvidia-smi' is not recognized"
    ps["disks"] = {"name": "SHPP41-2000GM", "bus": "NVMe", "media": "SSD",
                   "temp_c": None, "temp_max_c": None, "wear_pct": None}  # no counters, bare object
    m = cm.build_metrics(ps, None, RUNNER)
    assert m["gpu_temp_c"] is None and m["gpus"] == []
    assert m["nvme_temp_c"] is None and m["disks"][0]["name"] == "SHPP41-2000GM"
    assert "gpu" in m["probe_errors"] and m["wsl"] is None


def test_nvme_zero_temp_means_no_sensor_and_sata_ignored():
    ps = cm.parse_ps_output(PS_REAL)
    ps["disks"] = [{"name": "A", "bus": "NVMe", "temp_c": 0},
                   {"name": "B", "bus": "SATA", "temp_c": 90}]
    assert cm.build_metrics(ps, None, RUNNER)["nvme_temp_c"] is None


def test_nvidia_smi_variants():
    assert cm.parse_nvidia_smi("") == []
    g = cm.parse_nvidia_smi("NVIDIA GeForce RTX 4060, 55, 3 %, 2909 MiB, 8188 MiB")
    assert g[0]["temp_c"] == 55.0 and g[0]["util_pct"] == 3.0 and g[0]["mem_total_mb"] == 8188.0
    g = cm.parse_nvidia_smi("GPU A, [N/A], [Not Supported], 1, 2\nGPU B, 60, 1, 1, 2")
    assert g[0]["temp_c"] is None and g[1]["temp_c"] == 60.0
    with pytest.raises(cm.ProbeParseError):
        cm.parse_nvidia_smi("No devices were found, x")


def test_wsl_parse_and_failure():
    w = cm.parse_wsl_output(WSL_REAL)
    assert w["mem_total_mb"] == 15558 and w["mem_used_pct"] == pytest.approx(8.6, abs=0.1)
    assert w["loadavg"] == [0.06, 0.16, 0.17] and w["uptime_s"] == pytest.approx(11496.59)
    inactive = WSL_REAL.replace("runner_service=active", "runner_service=inactive")
    assert cm.parse_wsl_output(inactive)["runner_service"] == "inactive"
    with pytest.raises(cm.ProbeParseError):
        cm.parse_wsl_output("There is no distribution with the supplied name.")


def test_runner_from_api():
    data = {"total_count": 1, "runners": [{"name": "cubeasht-orchestrator", "status": "online", "busy": True}]}
    assert cm.runner_from_api(data) == {"name": "cubeasht-orchestrator", "status": "online", "busy": True}
    assert cm.runner_from_api({"runners": []})["status"] == "not_registered"
    with pytest.raises(cm.MonitorError):
        cm.runner_from_api({"message": "Bad credentials"})


# ---------------------------------------------------------------- alert windows

def _ok(minutes_ago, **kw):
    m = {"reachable": True, "gpu_temp_c": 50.0, "nvme_temp_c": 55.0, "cpu_temp_c": None,
         "cpu_temp_source": "unavailable", "ram_used_pct": 50.0, "disk_c_free_pct": 70.0,
         "disk_c_free_gb": 1400.0, "disk_c_total_gb": 2000.0, "cpu_load_pct": 5.0,
         "runner": dict(RUNNER), "wsl": {"runner_service": "active"}}
    m.update(kw)
    return {"ts": T0 - timedelta(minutes=minutes_ago), "ok": True, "metrics": m, "error": None}


def _down(minutes_ago):
    return {"ts": T0 - timedelta(minutes=minutes_ago), "ok": False,
            "metrics": {"reachable": False, "runner": None}, "error": "ssh unreachable (rc=255): timed out"}


def kinds(samples):
    return sorted(f["kind"] for f in cm.evaluate_alerts(samples, T0))


def test_all_green_no_findings():
    assert kinds([_ok(m) for m in (20, 15, 10, 5, 0)]) == []


def test_gpu_sustained_vs_blip():
    hot = [_ok(m, gpu_temp_c=88.0) for m in (10, 5, 0)]
    assert kinds(hot) == ["gpu_temp"]
    f = cm.evaluate_alerts(hot, T0)[0]
    assert f["latest"] == 88.0 and f["threshold"] == 85.0
    blip = [_ok(10, gpu_temp_c=88.0), _ok(5, gpu_temp_c=70.0), _ok(0, gpu_temp_c=88.0)]
    assert kinds(blip) == []


def test_sustained_needs_window_coverage():
    # only 5 min of (hot) history: not yet "sustained 10 min"
    assert kinds([_ok(5, gpu_temp_c=90.0), _ok(0, gpu_temp_c=90.0)]) == []
    # launchd jitter: oldest in-window sample 1 min after window start still counts
    jitter = [_ok(9, gpu_temp_c=90.0), _ok(4, gpu_temp_c=90.0), _ok(0, gpu_temp_c=90.0)]
    assert kinds(jitter) == ["gpu_temp"]
    # regression: the "10 min ago" sample is a hair OLDER than exactly 10 min
    late = [_ok(10.01, gpu_temp_c=90.0), _ok(5, gpu_temp_c=90.0), _ok(0, gpu_temp_c=90.0)]
    assert kinds(late) == ["gpu_temp"]
    # ...but a normal sample within the grace before the window still vetoes
    assert kinds([_ok(12, gpu_temp_c=60.0), _ok(5, gpu_temp_c=90.0), _ok(0, gpu_temp_c=90.0)]) == []


def test_nvme_threshold_70():
    assert kinds([_ok(m, nvme_temp_c=71.0) for m in (10, 5, 0)]) == ["nvme_temp"]
    assert kinds([_ok(m, nvme_temp_c=69.0) for m in (10, 5, 0)]) == []


def test_unreachable_sample_breaks_temperature_sustain():
    s = [_ok(10, gpu_temp_c=90.0), _down(5), _ok(0, gpu_temp_c=90.0)]
    assert "gpu_temp" not in kinds(s)


def test_cpu_temp_skipped_silently_without_source_and_fires_with_one():
    assert kinds([_ok(m) for m in (10, 5, 0)]) == []
    hot = [_ok(m, cpu_temp_c=87.0, cpu_temp_source="librehardwaremonitor") for m in (10, 5, 0)]
    assert kinds(hot) == ["cpu_temp"]


def test_disk_c_low_on_latest_reachable_sample():
    assert kinds([_ok(5), _ok(0, disk_c_free_pct=12.0, disk_c_free_gb=240.0)]) == ["disk_c"]
    assert kinds([_ok(0, disk_c_free_pct=15.0)]) == []
    # latest sample unreachable: still judged on the last reachable one
    assert kinds([_ok(5, disk_c_free_pct=10.0), _down(0)]) == ["disk_c"]


def test_ram_15_min():
    assert kinds([_ok(m, ram_used_pct=93.0) for m in (15, 10, 5, 0)]) == ["ram"]
    assert kinds([_ok(m, ram_used_pct=93.0) for m in (10, 5, 0)]) == []  # only 10 min
    assert kinds([_ok(m, ram_used_pct=90.0) for m in (15, 10, 5, 0)]) == []  # > not >=


def test_unreachable_30_min():
    assert kinds([_down(m) for m in (30, 25, 20, 15, 10, 5, 0)]) == ["unreachable"]
    assert kinds([_down(m) for m in (25, 20, 15, 10, 5, 0)]) == []  # 25 min only
    assert kinds([_ok(30)] + [_down(m) for m in (25, 20, 15, 10, 5, 0)]) == []
    f = cm.evaluate_alerts([_ok(40)] + [_down(m) for m in (30, 25, 20, 15, 10, 5, 0)], T0)[0]
    assert f["last_ok"] == "2026-10-07T11:20:00Z" and f["failed_samples"] == 7


def test_unparseable_probe_is_not_unreachable():
    s = [{"ts": T0 - timedelta(minutes=m), "ok": False, "metrics": {"reachable": True}, "error": "bad"}
         for m in (30, 20, 10, 0)]
    assert kinds(s) == []


def test_runner_offline_only_while_reachable():
    off = {"name": "cubeasht-orchestrator", "status": "offline", "busy": False}
    assert kinds([_ok(m, runner=off) for m in (30, 25, 20, 15, 10, 5, 0)]) == ["runner_offline"]
    # desktop asleep part of the window: that's the unreachable story, not a runner page
    mixed = [_ok(m, runner=off) for m in (30, 25, 20)] + [_down(m) for m in (15, 10, 5, 0)]
    assert "runner_offline" not in kinds(mixed)
    assert kinds([_ok(m, runner=off) for m in (20, 15, 10, 5, 0)]) == []  # 20 min only
    # gh failed (runner=None) on one sample: unknown is not offline
    s = [_ok(m, runner=off) for m in (30, 25, 20, 15, 10, 5)] + [_ok(0, runner=None)]
    assert kinds(s) == []


# ---------------------------------------------------------------- 2 runners (orch-console #58379)

R1 = "cubeasht-orchestrator"
R2 = "cubeasht-orchestrator-2"


def _rs(s1, s2, b1=False, b2=False):
    rs = [{"name": R1, "status": s1, "busy": b1}, {"name": R2, "status": s2, "busy": b2}]
    return {"runners": rs, "runner": dict(rs[0])}


WIN30 = (30, 25, 20, 15, 10, 5, 0)


def test_runners_from_api_label_filter_and_summary():
    lab = [{"name": "self-hosted"}, {"name": "cubeasht"}, {"name": "win-wsl"}]
    data = {"total_count": 3, "runners": [
        {"name": R2, "status": "offline", "busy": False, "labels": lab},
        {"name": "gzb-runner", "status": "online", "busy": True, "labels": [{"name": "self-hosted"}]},
        {"name": R1, "status": "online", "busy": True, "labels": lab}]}
    rs = cm.runners_from_api(data)
    assert rs == [{"name": R1, "status": "online", "busy": True},
                  {"name": R2, "status": "offline", "busy": False}]  # sorted, label-filtered
    assert cm.runner_summary(rs) == {"name": R1, "status": "online", "busy": True}
    # nobody carries the label: summary says not_registered (CI cannot run)
    assert cm.runners_from_api({"runners": []}) == []
    assert cm.runner_summary([])["status"] == "not_registered"
    with pytest.raises(cm.MonitorError):
        cm.runners_from_api({"message": "Bad credentials"})


def test_wsl_probe_records_every_runner_unit():
    two = WSL_REAL.replace(
        "runner_service=active\n",
        "runner_service=active\n"
        "runner_unit=actions.runner.sheikh-musa-wingmen-orchestrator.cubeasht-orchestrator-2.service=inactive\n"
        "runner_unit=actions.runner.sheikh-musa-wingmen-orchestrator.cubeasht-orchestrator.service=active\n")
    w = cm.parse_wsl_output(two)
    assert w["runner_service"] == "active"
    assert w["runner_services"] == {
        "actions.runner.sheikh-musa-wingmen-orchestrator.cubeasht-orchestrator-2.service": "inactive",
        "actions.runner.sheikh-musa-wingmen-orchestrator.cubeasht-orchestrator.service": "active"}
    assert "runner_services" not in cm.parse_wsl_output(WSL_REAL) or \
        cm.parse_wsl_output(WSL_REAL)["runner_services"] == {}


def test_two_runners_all_offline_pages_p1():
    fs = cm.evaluate_alerts([_ok(m, **_rs("offline", "offline")) for m in WIN30], T0)
    assert [f["kind"] for f in fs] == ["runner_offline"]
    f = fs[0]
    assert f["key"] == "cubeasht:runner_offline" and f["priority"] == "P1" and f["req"] is True
    assert f["offline"] == [R1, R2]
    subj, body = cm.render_page(f)
    assert "all 2" in subj.lower() and R2 in body


def test_two_runners_one_offline_pages_degraded_p2():
    fs = cm.evaluate_alerts([_ok(m, **_rs("online", "offline")) for m in WIN30], T0)
    assert [f["kind"] for f in fs] == ["runner_degraded"]
    f = fs[0]
    assert f["key"] == "cubeasht:runner_degraded" and f["priority"] == "P2"
    assert f["offline"] == [R2] and f["online"] == [R1]
    subj, body = cm.render_page(f)
    assert "1/2" in subj and body.startswith("TL;DR:") and "WHAT TO DO" in body
    # which runner is down may change mid-window: still degraded throughout
    flip = [_ok(m, **_rs("online", "offline")) for m in (30, 25, 20)] + \
           [_ok(m, **_rs("offline", "online")) for m in (15, 10, 5, 0)]
    assert kinds(flip) == ["runner_degraded"]


def test_two_runners_degraded_needs_30_min_and_reachability():
    assert kinds([_ok(m, **_rs("online", "offline")) for m in (20, 15, 10, 5, 0)]) == []
    blip = [_ok(m, **_rs("online", "offline")) for m in (30, 25, 20, 15, 10, 5)] + \
           [_ok(0, **_rs("online", "online"))]
    assert kinds(blip) == []
    asleep = [_ok(m, **_rs("online", "offline")) for m in (30, 25, 20)] + [_down(m) for m in (15, 10, 5, 0)]
    assert "runner_degraded" not in kinds(asleep) and "runner_offline" not in kinds(asleep)
    assert kinds([_ok(m, **_rs("online", "online")) for m in WIN30]) == []


def test_two_runners_degraded_then_all_offline_is_not_double_paged():
    # mixed window (degraded 15 min, then both offline 15 min): neither condition held for 30 min
    s = [_ok(m, **_rs("online", "offline")) for m in (30, 25, 20)] + \
        [_ok(m, **_rs("offline", "offline")) for m in (15, 10, 5, 0)]
    assert kinds(s) == []


def test_all_runners_unregistered_counts_as_offline():
    s = [_ok(m, runners=[], runner={"name": R1, "status": "not_registered", "busy": False}) for m in WIN30]
    assert kinds(s) == ["runner_offline"]


def test_send_page_uses_finding_priority(monkeypatch):
    calls = []
    import scripts.bus_send as bs
    monkeypatch.setattr(bs, "send", lambda **kw: calls.append(kw) or (7, "t"))
    f = cm.evaluate_alerts([_ok(m, **_rs("online", "offline")) for m in WIN30], T0)[0]
    assert cm._send_page(f) == 7
    assert calls[0]["priority"] == "P2" and calls[0]["req"] is False
    f = cm.evaluate_alerts([_ok(m, **_rs("offline", "offline")) for m in WIN30], T0)[0]
    cm._send_page(f)
    assert calls[1]["priority"] == "P1" and calls[1]["req"] is True
    assert all(c["from_agent"] == "cc-fleet-health" and c["to"] == "orch-console" for c in calls)


# ---------------------------------------------------------------- WSL page-cache picture

def test_vmmem_minus_wsl_used_derived():
    ps = cm.parse_ps_output(PS_REAL)  # vmmem_ws_bytes 2599096320 -> 2479 MB
    m = cm.build_metrics(ps, cm.parse_wsl_output(WSL_REAL), RUNNER)  # wsl used 1328 MB
    assert m["vmmem_minus_wsl_used_mb"] == 2479 - 1328
    assert cm.build_metrics(ps, None, RUNNER)["vmmem_minus_wsl_used_mb"] is None
    ps2 = dict(ps); ps2.pop("vmmem_ws_bytes"); ps2["vmmem_error"] = "no vmmem"
    assert cm.build_metrics(ps2, cm.parse_wsl_output(WSL_REAL), RUNNER)["vmmem_minus_wsl_used_mb"] is None


def test_ram_alert_body_mentions_wsl_cache():
    cache = dict(ram_used_pct=95.0, vmmem_ws_mb=12493, vmmem_minus_wsl_used_mb=10752,
                 wsl={"runner_service": "active", "mem_used_mb": 1741})
    f = cm.evaluate_alerts([_ok(m, **cache) for m in (15, 10, 5, 0)], T0)[0]
    assert f["kind"] == "ram" and f["vmmem_minus_wsl_used_mb"] == 10752
    body = cm.render_page(f)[1]
    assert "~10.5 GB is WSL cache" in body and "12.2 GB" in body
    # no vmmem data: no cache line, still renders
    f = cm.evaluate_alerts([_ok(m, ram_used_pct=95.0) for m in (15, 10, 5, 0)], T0)[0]
    assert "WSL cache" not in cm.render_page(f)[1]


def test_render_every_kind_has_tldr_and_one_line_subject():
    samples = ([_ok(m, gpu_temp_c=90.0, nvme_temp_c=75.0, ram_used_pct=95.0, disk_c_free_pct=5.0,
                    cpu_temp_c=90.0, cpu_temp_source="librehardwaremonitor") for m in (15, 10, 5, 0)])
    fs = cm.evaluate_alerts(samples, T0)
    fs += cm.evaluate_alerts([_down(m) for m in (30, 25, 20, 15, 10, 5, 0)], T0)
    fs += cm.evaluate_alerts([_ok(m, runner={"name": "r", "status": "offline", "busy": False})
                              for m in (30, 25, 20, 15, 10, 5, 0)], T0)
    assert sorted(f["kind"] for f in fs) == sorted(
        ["cpu_temp", "gpu_temp", "nvme_temp", "ram", "disk_c", "unreachable", "runner_offline"])
    for f in fs:
        subj, body = cm.render_page(f)
        assert "\n" not in subj and len(subj) < 120
        assert body.startswith("TL;DR:") and "WHAT TO DO" in body
    unreach = [f for f in fs if f["kind"] == "unreachable"][0]
    assert "asleep" in cm.render_page(unreach)[0]


# ---------------------------------------------------------------- dedup

def test_dedup_cooldown_and_clear(tmp_path):
    f = {"kind": "gpu_temp", "key": "cubeasht:gpu_temp"}
    to_page, st = cm.plan_pages([f], {}, T0)
    assert to_page == [f]
    st = cm.mark_paged(st, f["key"], T0)
    assert cm.plan_pages([f], st, T0 + timedelta(minutes=59))[0] == []
    assert cm.plan_pages([f], st, T0 + timedelta(minutes=60))[0] == [f]
    # resolved: key cleared, so a recurrence pages immediately
    _, cleared = cm.plan_pages([], st, T0 + timedelta(minutes=10))
    assert cleared == {}
    assert cm.plan_pages([f], cleared, T0 + timedelta(minutes=15))[0] == [f]
    # state file round-trip + corrupt state fails loud
    p = tmp_path / "s.json"
    cm.save_state(p, st)
    assert cm.load_state(p) == st
    p.write_text("{nope")
    with pytest.raises(cm.MonitorError):
        cm.load_state(p)


# ---------------------------------------------------------------- summary

def test_summary_math():
    day = date(2026, 10, 7)
    base = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    def s(h, gpu, nvme, load, busy, ok=True):
        return {"ts": base + timedelta(hours=h), "ok": ok, "error": None,
                "metrics": {"reachable": ok, "gpu_temp_c": gpu, "nvme_temp_c": nvme, "cpu_load_pct": load,
                            "ram_used_pct": 50.0, "cpu_temp_c": None,
                            "runner": {"busy": busy, "status": "online"}}}
    samples = [s(1, 50, 55, 10, False), s(2, 60, 60, 80, True), s(3, 70, 65, 90, True),
               {"ts": base + timedelta(hours=4), "ok": False, "error": "x", "metrics": {"reachable": False}},
               s(25, 99, 99, 99, True)]  # next day: excluded
    out = cm.summarize(samples, day)
    assert out["samples"] == 4 and out["reachable_samples"] == 3 and out["unreachable_samples"] == 1
    assert out["gpu_temp_c"] == {"min": 50.0, "avg": 60.0, "max": 70.0, "n": 3}
    assert out["nvme_temp_c"]["avg"] == 60.0
    assert out["cpu_load_pct"]["overall"] == {"min": 10.0, "avg": 60.0, "max": 90.0, "n": 3}
    assert out["cpu_load_pct"]["ci_busy"] == {"min": 80.0, "avg": 85.0, "max": 90.0, "n": 2}
    assert out["cpu_load_pct"]["ci_idle"]["n"] == 1
    assert "cpu_temp_c" not in out  # no source yet
    samples[0]["metrics"]["cpu_temp_c"] = 60.0
    assert cm.summarize(samples, day)["cpu_temp_c"] == {"min": 60.0, "avg": 60.0, "max": 60.0, "n": 1}


def test_summary_ci_busy_is_any_runner_busy():
    base = datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc)
    def s(h, load, b1, b2):
        m = {"reachable": True, "cpu_load_pct": load, **_rs("online", "online", b1, b2)}
        return {"ts": base + timedelta(hours=h), "ok": True, "error": None, "metrics": m}
    # runner 1 idle but runner 2 busy -> CI busy (the legacy `runner` field alone would say idle)
    out = cm.summarize([s(0, 10, False, False), s(1, 70, False, True), s(2, 90, True, True)],
                       date(2026, 10, 7))
    assert out["ci_busy_samples"] == 2
    assert out["cpu_load_pct"]["ci_busy"] == {"min": 70.0, "avg": 80.0, "max": 90.0, "n": 2}
    assert out["cpu_load_pct"]["ci_idle"] == {"min": 10.0, "avg": 10.0, "max": 10.0, "n": 1}


def test_summary_timezone_cut():
    # 2026-10-07 in Asia/Kuala_Lumpur (UTC+8) = 2026-10-06T16:00Z .. 2026-10-07T16:00Z
    lo, hi = cm.day_bounds(date(2026, 10, 7), "Asia/Kuala_Lumpur")
    assert lo == datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc)
    assert hi == datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- main(): fail loud / dry-run

def test_dry_run_writes_nothing(monkeypatch, tmp_path, capsys):
    metrics = cm.build_metrics(cm.parse_ps_output(PS_REAL), cm.parse_wsl_output(WSL_REAL), RUNNER)
    monkeypatch.setattr(cm, "collect", lambda: (True, metrics, None, []))
    def no_db():
        raise RuntimeError('relation "host_metrics" does not exist')
    monkeypatch.setattr(cm, "connect", no_db)
    monkeypatch.setattr(cm, "_send_page", lambda f: pytest.fail("dry-run must not page"))
    state = tmp_path / "state.json"
    assert cm.main(["--dry-run", "--state-file", str(state)]) == 0
    out = capsys.readouterr().out
    assert "findings=0" in out and "dry-run: nothing inserted" in out and "history unavailable" in out
    assert not state.exists()


def test_live_db_failure_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cm, "collect", lambda: (False, {"reachable": False}, "ssh unreachable", []))
    def boom():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(cm, "connect", boom)
    assert cm.main(["--state-file", str(tmp_path / "s.json")]) == 3
    assert "FAIL LOUD" in capsys.readouterr().err


# ---------------------------------------------------------------- DB: migration + live path + retention

MIGRATION = ROOT / "migrations" / "096_host_metrics.sql"


@pytest.fixture
def hm_conn(pg_dsn):
    import psycopg2
    admin = psycopg2.connect(pg_dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS cubemon_test")
        cur.execute("CREATE DATABASE cubemon_test")
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') "
                        f"THEN CREATE ROLE {role}; END IF; END $$")
    admin.close()
    conn = psycopg2.connect(pg_dsn.replace("dbname=postgres", "dbname=cubemon_test"))
    with conn.cursor() as cur:
        cur.execute(MIGRATION.read_text())
        cur.execute(MIGRATION.read_text())  # re-run-safe
    conn.commit()
    yield conn
    conn.close()


def test_migration_shape_and_privileges(hm_conn):
    with hm_conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('anon','public.host_metrics','SELECT'), "
                    "has_table_privilege('authenticated','public.host_metrics','INSERT'), "
                    "(SELECT relrowsecurity FROM pg_class WHERE relname='host_metrics'), "
                    "(SELECT count(*) FROM pg_indexes WHERE indexname='host_metrics_host_ts_idx')")
        assert cur.fetchone() == (False, False, True, 1)
    hm_conn.rollback()


def test_insert_fetch_and_retention_bound(hm_conn):
    rid, ts = cm.insert_sample(hm_conn, "cubeasht", True, {"reachable": True, "gpu_temp_c": 52.0}, None)
    cm.insert_sample(hm_conn, "cubeasht", False, {"reachable": False}, "ssh unreachable")
    got = cm.fetch_samples(hm_conn, "cubeasht", ts - timedelta(minutes=1))
    assert [g["ok"] for g in got] == [True, False] and got[0]["metrics"]["gpu_temp_c"] == 52.0

    with hm_conn.cursor() as cur:
        cur.execute("INSERT INTO host_metrics (host, ts, metrics, ok) "
                    "SELECT 'cubeasht', now() - interval '31 days' - (g || ' min')::interval, '{}', true "
                    "FROM generate_series(1, 25) g")
        cur.execute("INSERT INTO host_metrics (host, ts, metrics, ok) "
                    "VALUES ('otherhost', now() - interval '40 days', '{}', true), "
                    "('cubeasht', now() - interval '29 days', '{}', true)")
    hm_conn.commit()
    assert cm.prune(hm_conn, limit=10) == 10       # bounded
    assert cm.prune(hm_conn, limit=10) == 10
    assert cm.prune(hm_conn, limit=10) == 5
    assert cm.prune(hm_conn, limit=10) == 0
    with hm_conn.cursor() as cur:
        cur.execute("SELECT host, count(*) FROM host_metrics GROUP BY host ORDER BY host")
        # 29-day-old row + 2 fresh kept; other host's rows never touched by this collector
        assert cur.fetchall() == [("cubeasht", 3), ("otherhost", 1)]
    hm_conn.rollback()


def test_live_run_pages_once_then_cooldown(monkeypatch, hm_conn, tmp_path, capsys):
    # 2 prior hot samples + this run's hot sample = sustained 10 min GPU
    with hm_conn.cursor() as cur:
        for mins in (10, 5):
            cur.execute("INSERT INTO host_metrics (host, ts, metrics, ok) VALUES "
                        "('cubeasht', now() - make_interval(mins => %s), %s::jsonb, true)",
                        (mins, json.dumps({"reachable": True, "gpu_temp_c": 90.0})))
    hm_conn.commit()
    metrics = {"reachable": True, "gpu_temp_c": 91.0, "runner": dict(RUNNER)}

    class NoClose:
        def __init__(self, c): self.c = c
        def __getattr__(self, n): return getattr(self.c, n)
        def close(self): pass

    monkeypatch.setattr(cm, "collect", lambda: (True, metrics, None, []))
    monkeypatch.setattr(cm, "connect", lambda: NoClose(hm_conn))
    sent = []
    monkeypatch.setattr(cm, "_send_page", lambda f: sent.append(f) or 4242)
    state = tmp_path / "s.json"
    assert cm.main(["--state-file", str(state)]) == 0
    assert [f["kind"] for f in sent] == ["gpu_temp"]
    assert "cubeasht:gpu_temp" in json.loads(state.read_text())
    assert cm.main(["--state-file", str(state)]) == 0   # cooldown: no second page
    assert len(sent) == 1

    # gh failed this run: sample still stored, exit 4 (loud)
    monkeypatch.setattr(cm, "collect", lambda: (True, metrics, None, ["runner status: gh api failed rc=1"]))
    assert cm.main(["--state-file", str(state)]) == 4
    assert "FAIL LOUD (sample stored)" in capsys.readouterr().err
