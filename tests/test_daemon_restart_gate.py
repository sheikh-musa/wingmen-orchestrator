"""daemon_restart_gate -- backlog#124's DAEMONS-bucket restart-gate ("catch A",
orch-console ruling bus #74954/#75001). Core claim under test: a daemon's process
start time compared against its watched source files' mtime correctly tells fresh
code apart from stale in-memory code, including the pre-deployment vacuous-pass
case and the not-running/race cases."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import daemon_restart_gate as g  # noqa: E402


class _FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _fake_run(responses):
    """responses: dict mapping a command-prefix tuple to a _FakeProc."""
    def run(cmd, **kw):
        for prefix, proc in responses.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return proc
        raise AssertionError(f"unexpected command: {cmd!r}")
    return run


LAUNCHCTL_RUNNING = (
    '{\n\t"Label" = "dev.wingmen.wake-backstop-sweep";\n\t"LimitLoadToSessionType" = "Aqua";\n'
    '\t"PID" = 4242;\n\t"LastExitStatus" = 0;\n}\n'
)
LAUNCHCTL_LOADED_NOT_RUNNING = (
    '{\n\t"Label" = "dev.wingmen.lane-wedge-watchdog";\n\t"LastExitStatus" = 0;\n}\n'
)


# ── launchd_pid ──────────────────────────────────────────────────────────────

def test_launchd_pid_parses_real_macos_list_output():
    run = _fake_run({("launchctl", "list"): _FakeProc(0, LAUNCHCTL_RUNNING)})
    assert g.launchd_pid("dev.wingmen.wake-backstop-sweep", run=run) == 4242


def test_launchd_pid_none_when_loaded_but_no_pid_key():
    run = _fake_run({("launchctl", "list"): _FakeProc(0, LAUNCHCTL_LOADED_NOT_RUNNING)})
    assert g.launchd_pid("dev.wingmen.lane-wedge-watchdog", run=run) is None


def test_launchd_pid_none_when_not_loaded_at_all():
    run = _fake_run({("launchctl", "list"): _FakeProc(1, "")})
    assert g.launchd_pid("dev.wingmen.nonexistent", run=run) is None


# ── process_start_epoch ──────────────────────────────────────────────────────

def test_process_start_epoch_parses_real_macos_ps_lstart_format():
    # macOS pads single-digit days with a leading space.
    run = _fake_run({("ps",): _FakeProc(0, "Thu Oct 24  9:12:03 2026\n")})
    epoch = g.process_start_epoch(4242, run=run)
    assert epoch is not None
    import datetime
    assert datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S") == "2026-10-24 09:12:03"


def test_process_start_epoch_none_when_pid_gone():
    run = _fake_run({("ps",): _FakeProc(1, "")})
    assert g.process_start_epoch(99999, run=run) is None


# ── check(): the real decision logic ─────────────────────────────────────────

def test_running_fresh_when_process_started_after_source_mtime(tmp_path):
    src = tmp_path / "daemon.py"
    src.write_text("x = 1")
    os.utime(src, (time.time() - 3600, time.time() - 3600))  # mtime: 1h ago
    recent = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - 60))
    run = _fake_run({
        ("launchctl", "list"): _FakeProc(0, LAUNCHCTL_RUNNING),
        ("ps",): _FakeProc(0, recent + "\n"),
    })
    result = g.check("dev.wingmen.wake-backstop-sweep", [str(src)], run=run)
    assert result.ok is True
    assert result.status == "RUNNING_FRESH"


def test_stale_when_process_started_before_source_mtime(tmp_path):
    src = tmp_path / "daemon.py"
    src.write_text("x = 1")
    os.utime(src, (time.time() - 60, time.time() - 60))  # mtime: just now (recent deploy)
    old = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - 3600))
    run = _fake_run({
        ("launchctl", "list"): _FakeProc(0, LAUNCHCTL_RUNNING),
        ("ps",): _FakeProc(0, old + "\n"),
    })
    result = g.check("dev.wingmen.wake-backstop-sweep", [str(src)], run=run)
    assert result.ok is False
    assert result.status == "STALE_NEEDS_RESTART"
    assert "bounce" in result.message.lower() or "kickstart" in result.message.lower()


def test_source_mtime_uses_the_newest_of_multiple_files(tmp_path):
    old_src = tmp_path / "old.py"
    old_src.write_text("x = 1")
    os.utime(old_src, (time.time() - 7200, time.time() - 7200))
    new_src = tmp_path / "new.py"
    new_src.write_text("x = 1")
    os.utime(new_src, (time.time() - 60, time.time() - 60))  # the newest -- governs
    between = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - 3600))
    run = _fake_run({
        ("launchctl", "list"): _FakeProc(0, LAUNCHCTL_RUNNING),
        ("ps",): _FakeProc(0, between + "\n"),
    })
    result = g.check("dev.wingmen.wake-backstop-sweep", [str(old_src), str(new_src)], run=run)
    assert result.ok is False
    assert result.status == "STALE_NEEDS_RESTART"


def test_not_running_fails_without_pre_deployment_flag(tmp_path):
    src = tmp_path / "daemon.py"
    src.write_text("x = 1")
    run = _fake_run({("launchctl", "list"): _FakeProc(1, "")})
    result = g.check("dev.wingmen.lane-wedge-watchdog", [str(src)], run=run)
    assert result.ok is False
    assert result.status == "NOT_RUNNING"


def test_not_running_satisfied_with_pre_deployment_flag(tmp_path):
    src = tmp_path / "daemon.py"
    src.write_text("x = 1")
    run = _fake_run({("launchctl", "list"): _FakeProc(1, "")})
    result = g.check("dev.wingmen.lane-wedge-watchdog", [str(src)], pre_deployment=True, run=run)
    assert result.ok is True
    assert result.status == "PRE_DEPLOYMENT_SATISFIED"


def test_pid_race_reported_as_not_running(tmp_path):
    src = tmp_path / "daemon.py"
    src.write_text("x = 1")
    run = _fake_run({
        ("launchctl", "list"): _FakeProc(0, LAUNCHCTL_RUNNING),
        ("ps",): _FakeProc(1, ""),  # pid vanished between the two calls
    })
    result = g.check("dev.wingmen.wake-backstop-sweep", [str(src)], run=run)
    assert result.ok is False
    assert result.status == "NOT_RUNNING"


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_main_exit_0_when_check_ok(monkeypatch, capsys):
    monkeypatch.setattr(g, "check", lambda *a, **k: g.RestartGateResult(True, "RUNNING_FRESH", "fine"))
    rc = g.main(["dev.wingmen.wake-backstop-sweep", "--source", "x.py"])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_main_exit_1_when_check_fails(monkeypatch, capsys):
    monkeypatch.setattr(g, "check", lambda *a, **k: g.RestartGateResult(False, "STALE_NEEDS_RESTART", "stale"))
    rc = g.main(["dev.wingmen.wake-backstop-sweep", "--source", "x.py"])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_json_output(monkeypatch, capsys):
    monkeypatch.setattr(g, "check", lambda *a, **k: g.RestartGateResult(True, "RUNNING_FRESH", "fine"))
    g.main(["dev.wingmen.wake-backstop-sweep", "--source", "x.py", "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "status": "RUNNING_FRESH", "message": "fine"}


def test_main_passes_pre_deployment_flag_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(g, "check", lambda label, sources, pre_deployment=False, run=None:
                         captured.update(pre_deployment=pre_deployment) or
                         g.RestartGateResult(True, "PRE_DEPLOYMENT_SATISFIED", "ok"))
    g.main(["dev.wingmen.lane-wedge-watchdog", "--source", "x.py", "--pre-deployment"])
    assert captured["pre_deployment"] is True
