"""scripts/cubeasht_daily_digest.py — ask#2207 daily cubeasht health roll-up.
render_digest()/default_date() are pure; _already_sent/_mark_sent are a trivial
per-date dedup; main()'s DB fetch (build_summary) and bus post (send_digest) are
monkeypatched so nothing here touches a DB or the bus."""
import importlib
import json
from datetime import date, datetime, timezone

import pytest

dg = importlib.import_module("scripts.cubeasht_daily_digest")

SAMPLE = {
    "host": "cubeasht", "date": "2026-10-12", "tz": "UTC",
    "samples": 263, "reachable_samples": 262, "unreachable_samples": 1,
    "gpu_temp_c": {"min": 42.0, "avg": 45.6, "max": 56.0, "n": 262},
    "nvme_temp_c": {"min": 50.0, "avg": 52.8, "max": 58.0, "n": 262},
    "cpu_load_pct": {
        "overall": {"min": 0.0, "avg": 5.6, "max": 36.0, "n": 262},
        "ci_busy": {"min": 0.0, "avg": 7.2, "max": 35.0, "n": 43},
        "ci_idle": {"min": 0.0, "avg": 5.3, "max": 36.0, "n": 219},
    },
    "ram_used_pct": {"min": 64.3, "avg": 74.2, "max": 85.9, "n": 262},
    "ci_busy_samples": 43,
}


# ── default_date: yesterday UTC ───────────────────────────────────────────────
def test_default_date_is_yesterday_utc():
    assert dg.default_date(datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)) == date(2026, 10, 12)


def test_default_date_crosses_month_boundary():
    assert dg.default_date(datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc)) == date(2026, 10, 31)


# ── render_digest: pure formatting ────────────────────────────────────────────
def test_render_subject_carries_date_and_tz():
    subj, _ = dg.render_digest(SAMPLE)
    assert "2026-10-12" in subj and "UTC" in subj


def test_render_tldr_has_peaks_and_ci():
    _, body = dg.render_digest(SAMPLE)
    assert "TL;DR" in body
    assert "peak GPU 56.0°C" in body
    assert "peak NVMe 58.0°C" in body
    assert "RAM peaked 85.9%" in body
    assert "CPU peaked 36.0%" in body
    assert "CI busy in 43 sample-windows" in body


def test_render_has_per_metric_lines_and_ci_split():
    _, body = dg.render_digest(SAMPLE)
    assert "GPU temp:" in body and "NVMe temp:" in body
    assert "during CI" in body and "idle" in body
    assert "RAM used:" in body


def test_render_body_ends_with_parseable_json():
    _, body = dg.render_digest(SAMPLE)
    blob = body.split("(raw summary JSON)", 1)[1]
    assert json.loads(blob)["samples"] == 263


def test_render_includes_cpu_temp_only_when_present():
    _, body = dg.render_digest(SAMPLE)
    assert "CPU temp:" not in body
    s = dict(SAMPLE, cpu_temp_c={"min": 40.0, "avg": 44.0, "max": 60.0, "n": 262})
    _, body2 = dg.render_digest(s)
    assert "CPU temp:" in body2 and "60.0°C" in body2


def test_render_zero_samples_warns_loudly():
    s = {"host": "cubeasht", "date": "2026-10-12", "tz": "UTC",
         "samples": 0, "reachable_samples": 0, "unreachable_samples": 0,
         "gpu_temp_c": None, "nvme_temp_c": None,
         "cpu_load_pct": {"overall": None, "ci_busy": None, "ci_idle": None},
         "ram_used_pct": None, "ci_busy_samples": 0}
    subj, body = dg.render_digest(s)
    assert "2026-10-12" in subj
    assert "ZERO samples" in body
    assert json.loads(body.split("(raw summary JSON)", 1)[1])["samples"] == 0


# ── dedup ─────────────────────────────────────────────────────────────────────
def test_already_sent_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "nope.json")
    assert dg._already_sent("2026-10-12") is False


def test_mark_then_already_sent_true(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    dg._mark_sent("2026-10-12")
    assert dg._already_sent("2026-10-12") is True
    assert dg._already_sent("2026-10-13") is False


def test_already_sent_false_on_corrupt_state(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text("{not json")
    monkeypatch.setattr(dg, "STATE_FILE", f)
    assert dg._already_sent("2026-10-12") is False


# ── main ──────────────────────────────────────────────────────────────────────
def _no_send(*a, **k):
    raise AssertionError("must not send")


def test_main_dry_run_prints_and_does_not_send(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(dg, "build_summary", lambda day, tz: SAMPLE)
    monkeypatch.setattr(dg, "send_digest", _no_send)
    rc = dg.main(["--dry-run", "--date", "2026-10-12"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "TL;DR" in out
    assert not (tmp_path / "s.json").exists()  # nothing stamped


def test_main_skips_when_already_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    dg._mark_sent("2026-10-12")
    monkeypatch.setattr(dg, "build_summary", lambda day, tz: SAMPLE)
    monkeypatch.setattr(dg, "send_digest", _no_send)
    assert dg.main(["--date", "2026-10-12"]) == 0


def test_main_sends_and_stamps(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(dg, "build_summary", lambda day, tz: SAMPLE)
    sent = {}
    monkeypatch.setattr(dg, "send_digest",
                        lambda subj, body: sent.update(subj=subj, body=body) or 4242)
    rc = dg.main(["--date", "2026-10-12"])
    assert rc == 0
    assert "2026-10-12" in sent["subj"]
    assert dg._already_sent("2026-10-12") is True  # stamped -> no double-send tomorrow


def test_main_force_resends_even_if_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    dg._mark_sent("2026-10-12")
    monkeypatch.setattr(dg, "build_summary", lambda day, tz: SAMPLE)
    calls = []
    monkeypatch.setattr(dg, "send_digest", lambda s, b: calls.append(1) or 7)
    assert dg.main(["--force", "--date", "2026-10-12"]) == 0
    assert calls == [1]


def test_main_bad_date_returns_2(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    assert dg.main(["--date", "not-a-date"]) == 2


def test_main_build_summary_failure_is_loud_rc3(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    def boom(day, tz):
        raise RuntimeError("db down")
    monkeypatch.setattr(dg, "build_summary", boom)
    assert dg.main(["--date", "2026-10-12"]) == 3


# ── 3-host digest (op#35975, orch-console #83733) ────────────────────────────
MINI_SAMPLE = {
    "host": "mini", "date": "2026-10-12", "tz": "UTC",
    "samples": 288, "reachable_samples": 288,
    "mem_real_used_pct": {"min": 40.0, "avg": 55.0, "max": 70.0, "n": 288},
    "disk_free_pct": {"min": 38.0, "avg": 40.0, "max": 42.0, "n": 288},
    "cpu_load_pct": {"min": 20.0, "avg": 80.0, "max": 150.0, "n": 288},
    "claude_sessions": {"min": 5, "avg": 20, "max": 30, "n": 288},
    "swap_used_pct": {"min": 60.0, "avg": 69.0, "max": 75.0, "n": 288},
}
GZB_SAMPLE = {
    "host": "gzb", "date": "2026-10-12", "tz": "UTC",
    "samples": 288, "reachable_samples": 285, "unreachable_samples": 3,
    "mem_real_used_pct": {"min": 10.0, "avg": 16.0, "max": 25.0, "n": 285},
    "disk_free_pct": {"min": 85.0, "avg": 89.0, "max": 92.0, "n": 285},
    "cpu_load_pct": {"min": 10.0, "avg": 60.0, "max": 95.0, "n": 285},
    "cpu_temp_c": {"min": 50.0, "avg": 58.0, "max": 65.0, "n": 285},
    "claude_sessions": {"min": 2, "avg": 7, "max": 10, "n": 285},
}


def test_render_generic_section_healthy_host():
    out = dg.render_generic_section(MINI_SAMPLE)
    assert "mini" in out
    assert "real memory used" in out
    assert "70.0" in out  # peak mem pct
    assert "claude sessions" in out


def test_render_generic_section_zero_samples():
    s = {"host": "mini", "samples": 0}
    out = dg.render_generic_section(s)
    assert "ZERO samples" in out


def test_render_generic_section_includes_temp_only_when_present():
    out_gzb = dg.render_generic_section(GZB_SAMPLE)
    assert "CPU temp" in out_gzb
    out_mini = dg.render_generic_section(MINI_SAMPLE)
    assert "CPU temp" not in out_mini


def test_build_summary_all_hosts_partial_failure_still_returns(monkeypatch):
    monkeypatch.setattr(dg, "build_summary", lambda day, tz: SAMPLE)
    monkeypatch.setattr(dg, "build_summary_mini", lambda day, tz: MINI_SAMPLE)
    def boom(day, tz):
        raise RuntimeError("gzb ssh timeout")
    monkeypatch.setattr(dg, "build_summary_gzb", boom)
    out = dg.build_summary_all_hosts(date(2026, 10, 12), "UTC")
    assert out["cubeasht"] == SAMPLE
    assert out["mini"] == MINI_SAMPLE
    assert "could not measure" in out["gzb"]


def test_build_summary_all_hosts_raises_when_all_fail(monkeypatch):
    def boom(day, tz):
        raise RuntimeError("down")
    monkeypatch.setattr(dg, "build_summary", boom)
    monkeypatch.setattr(dg, "build_summary_mini", boom)
    monkeypatch.setattr(dg, "build_summary_gzb", boom)
    with pytest.raises(dg.DigestError):
        dg.build_summary_all_hosts(date(2026, 10, 12), "UTC")


def test_render_digest_all_hosts_includes_all_three_and_tldr():
    summaries = {"cubeasht": SAMPLE, "mini": MINI_SAMPLE, "gzb": GZB_SAMPLE}
    subject, body = dg.render_digest_all_hosts(summaries, date(2026, 10, 12), "UTC")
    assert "2026-10-12" in subject
    assert "fleet 3-host" in subject
    assert "TL;DR" in body
    assert "## cubeasht" in body and "## mini" in body and "## gzb" in body
    assert "peak GPU 56.0" in body  # cubeasht's detailed render is reused verbatim


def test_render_digest_all_hosts_shows_could_not_measure_inline():
    summaries = {"cubeasht": SAMPLE, "mini": "could not measure -- SystemExit: no DATABASE_URL",
                 "gzb": GZB_SAMPLE}
    _, body = dg.render_digest_all_hosts(summaries, date(2026, 10, 12), "UTC")
    assert "could not measure" in body


def test_main_dry_run_three_host_digest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(dg, "build_summary", lambda day, tz: SAMPLE)
    monkeypatch.setattr(dg, "build_summary_mini", lambda day, tz: MINI_SAMPLE)
    monkeypatch.setattr(dg, "build_summary_gzb", lambda day, tz: GZB_SAMPLE)
    monkeypatch.setattr(dg, "send_digest", _no_send)
    rc = dg.main(["--dry-run", "--date", "2026-10-12"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## mini" in out and "## gzb" in out
