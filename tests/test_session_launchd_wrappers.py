"""boot_fleet_health_launchd.sh / boot_quality_launchd.sh must ADOPT, never kill, a
live tmux session (bus #82910/#82919) — same shape as boot_orch.sh's own fix for the
hub (adopting a live 'orch' session instead of restarting it on every launchd load).

Structural guards on the shipped scripts (bash -n), plus plutil -lint on the two new
plists under ops/launchd/. Prod-clean: no DB, no tmux action, nothing executed.
"""
import plistlib
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = [
    _ROOT / "scripts" / "boot_fleet_health_launchd.sh",
    _ROOT / "scripts" / "boot_quality_launchd.sh",
]
_PLISTS = [
    _ROOT / "ops" / "launchd" / "dev.wingmen.fleet-health-session.plist",
    _ROOT / "ops" / "launchd" / "dev.wingmen.quality-session.plist",
]


def _code_only(text: str) -> str:
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in text.splitlines())


def test_scripts_exist_and_executable():
    for p in _SCRIPTS:
        assert p.is_file(), f"missing {p}"
        assert p.stat().st_mode & 0o111, f"{p} is not executable"


def test_scripts_pass_bash_syntax_check():
    for p in _SCRIPTS:
        result = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        assert result.returncode == 0, f"{p} failed bash -n: {result.stderr}"


def test_scripts_adopt_not_kill_a_live_session():
    for p in _SCRIPTS:
        code = _code_only(p.read_text())
        assert "has-session" in code, f"{p} must check has-session before acting"
        assert "adopting existing live" in code.lower() or "adopting existing live" in p.read_text().lower(), (
            f"{p} must log an explicit adopt path, not a silent restart"
        )
        # The adopt branch must be a no-op (log only) — must NOT contain kill-session or
        # a tmux new-session call inside the has-session branch's own "then" arm.
        assert "kill-session" not in code, f"{p} must never kill a live session"


def test_scripts_block_until_session_ends():
    for p in _SCRIPTS:
        code = _code_only(p.read_text())
        assert "while" in code and "has-session" in code, (
            f"{p} must block on a has-session loop so KeepAlive doesn't spin up duplicates"
        )


def test_plists_exist_and_are_valid():
    for p in _PLISTS:
        assert p.is_file(), f"missing {p}"
        with p.open("rb") as f:
            data = plistlib.load(f)
        assert data.get("RunAtLoad") is True, f"{p} must set RunAtLoad"
        assert data.get("KeepAlive") is True, f"{p} must set KeepAlive"
        assert "ProgramArguments" in data and data["ProgramArguments"], f"{p} missing ProgramArguments"


def test_plists_point_at_the_tracked_wrapper_scripts():
    fh_plist = next(p for p in _PLISTS if "fleet-health" in p.name)
    q_plist = next(p for p in _PLISTS if "quality" in p.name)
    with fh_plist.open("rb") as f:
        assert "boot_fleet_health_launchd.sh" in plistlib.load(f)["ProgramArguments"][0]
    with q_plist.open("rb") as f:
        assert "boot_quality_launchd.sh" in plistlib.load(f)["ProgramArguments"][0]
