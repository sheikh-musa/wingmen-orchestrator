"""fc-v63 (Musa op#20684/20687/20692): lane BOOT / STAND-DOWN endpoints on the Mini
console + the scoped apply-armed relaxation (no bridge-arm for the REVERSIBLE
token/model switch; flag + typed confirm + gazzabyte fail-closed stay).

Hermetic: same stdlib-server fixture shape as test_app.py; subprocess + DB patched.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "test-console-token")
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    monkeypatch.setattr(db, "fetch_messages", lambda limit=50, thread=None, agent=None: [])
    monkeypatch.setattr(db, "fetch_lanes", lambda: [])
    monkeypatch.setattr(db, "fetch_fleet_lane_names", lambda: ["cosem-tdu", "irsyad-worker-2"])
    # a fresh cooldown table per test so runs never bleed into each other
    monkeypatch.setattr(console_app, "_LANE_ACTION_LAST_RUN", {})
    monkeypatch.setattr(console_app, "_SWITCH_LAST_RUN", {})
    srv = console_app.make_server(host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base
    srv.shutdown()


def H(tok="test-console-token"):
    return {"Authorization": f"Bearer {tok}"}


OK = MagicMock(returncode=0, stdout="BOOTED cosem-tdu → tmux session 'cosem-tdu' (/x)\n", stderr="")


# ---------------------------------------------------------------- lane-boot / lane-down
@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_requires_auth(server, route):
    with patch("subprocess.run") as run:
        r = httpx.post(server + route, json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 401
    run.assert_not_called()


@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_bad_charset_400_no_subprocess(server, route):
    with patch("subprocess.run") as run:
        r = httpx.post(server + route, headers=H(), json={"session": "; rm -rf /", "confirm": "; rm -rf /"}, timeout=5)
    assert r.status_code == 400
    run.assert_not_called()


@pytest.mark.parametrize("target", ["nazim", "cai", "orch", "fleet-health", "quality", "hub",
                                    "cc-orchestrator", "cc-fleet-health", "cc-cosem-tdu"])
@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_singleton_protected_403_no_subprocess(server, route, target):
    """A singleton body / bus id can never be booted or ended from the console —
    refused BEFORE the roster lookup and before any subprocess."""
    with patch("subprocess.run") as run, patch.object(db, "fetch_fleet_lane_names") as roster:
        r = httpx.post(server + route, headers=H(), json={"session": target, "confirm": target}, timeout=5)
    assert r.status_code == 403
    run.assert_not_called()
    roster.assert_not_called()


@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_unknown_lane_400_no_subprocess(server, route):
    with patch("subprocess.run") as run:
        r = httpx.post(server + route, headers=H(), json={"session": "not-a-lane", "confirm": "not-a-lane"}, timeout=5)
    assert r.status_code == 400 and r.json()["error"] == "unknown lane"
    run.assert_not_called()


@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_confirm_mismatch_400_no_subprocess(server, route):
    with patch("subprocess.run") as run:
        r = httpx.post(server + route, headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-td"}, timeout=5)
    assert r.status_code == 400 and "confirm" in r.json()["error"]
    run.assert_not_called()


def test_lane_action_roster_unavailable_503_fail_closed(server, monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "fetch_fleet_lane_names", boom)
    with patch("subprocess.run") as run:
        r = httpx.post(server + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 503
    run.assert_not_called()


def test_lane_boot_valid_runs_lanes_sh_up_exactly(server):
    with patch("subprocess.run", return_value=OK) as run:
        r = httpx.post(server + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["action"] == "boot"
    argv = run.call_args.args[0]
    assert argv[0] == "bash" and argv[1].endswith("scripts/lanes.sh")
    assert argv[2:] == ["up", "cosem-tdu"]
    assert "--force" not in argv and "--kill" not in argv
    env = run.call_args.kwargs["env"]
    assert env.get("RESET_FORCE") is None and env.get("ACTOR")


def test_lane_down_valid_runs_lanes_sh_down_kill_gates_inside(server):
    """Stand-down = `lanes.sh down <lane> --kill` — the ONLY flag is --kill (the
    'actually do it' switch); every gate lives inside lane_winddown.py and fails closed."""
    ok = MagicMock(returncode=0, stdout="WIND-DOWN OK: idle, drained, fresh handoff\nended tmux session 'cosem-tdu'\n", stderr="")
    with patch("subprocess.run", return_value=ok) as run:
        r = httpx.post(server + "/api/lane-down", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 200 and r.json()["action"] == "down"
    argv = run.call_args.args[0]
    assert argv[2:] == ["down", "cosem-tdu", "--kill"]
    assert "--force" not in argv


def test_lane_down_gate_refusal_is_a_legible_409(server):
    refused = MagicMock(returncode=1, stdout="REFUSED: 'cosem-tdu' is BUSY (mid-turn)\n", stderr="")
    with patch("subprocess.run", return_value=refused):
        r = httpx.post(server + "/api/lane-down", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 409
    assert "BUSY" in r.json()["tail"]


def test_lane_boot_double_tap_cooldown_429(server):
    with patch("subprocess.run", return_value=OK):
        r1 = httpx.post(server + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
        r2 = httpx.post(server + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r1.status_code == 200 and r2.status_code == 429


# ---------------------------------------------------------------- apply-armed (op#20692)
def _armed_patches():
    return (
        patch.object(console_app, "_resolve_armed_apply", return_value=(["bash", "/x/switch_lane_token.sh", "cosem-tdu", "/k"], "musa", None)),
        patch.object(console_app, "_lane_busy", return_value=False),
        patch.object(console_app, "_r4_current_arm", return_value=None),   # NO bridge-arm exists
    )


def test_apply_armed_503_without_r4_flag(server, monkeypatch):
    monkeypatch.setattr(console_app, "_R4_ENABLED", False)
    p1, p2, p3 = _armed_patches()
    with p1, p2, p3, patch("subprocess.run") as run:
        r = httpx.post(server + "/api/apply-armed", headers=H(),
                       json={"session": "cosem-tdu", "kind": "token", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 503
    run.assert_not_called()


def test_apply_armed_confirm_mismatch_400_no_subprocess(server, monkeypatch):
    monkeypatch.setattr(console_app, "_R4_ENABLED", True)
    p1, p2, p3 = _armed_patches()
    with p1, p2, p3, patch("subprocess.run") as run:
        r = httpx.post(server + "/api/apply-armed", headers=H(),
                       json={"session": "cosem-tdu", "kind": "token", "confirm": "cosem-td"}, timeout=5)
    assert r.status_code == 400
    run.assert_not_called()


def test_apply_armed_fires_with_flag_and_typed_confirm_and_NO_bridge_arm(server, monkeypatch):
    """The op#20692 relaxation, proven: CONSOLE_R4_ENABLED=1 + confirm==session is
    sufficient; _r4_current_arm is never consulted and arm=None is passed to the
    resolver (which keeps every other rule: gazzabyte fp, pointer must resolve)."""
    monkeypatch.setattr(console_app, "_R4_ENABLED", True)
    p1, p2, p3 = _armed_patches()
    ok = MagicMock(returncode=0, stdout="switched\n", stderr="")
    with p1 as resolve, p2, p3 as arm, patch("subprocess.run", return_value=ok) as run:
        r = httpx.post(server + "/api/apply-armed", headers=H(),
                       json={"session": "cosem-tdu", "kind": "token", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 200 and r.json()["ok"] is True
    arm.assert_not_called()
    assert resolve.call_args.args == ("cosem-tdu", "token", None)
    argv = run.call_args.args[0]
    assert argv[1].endswith("switch_lane_token.sh") and "--force" not in argv
    env = run.call_args.kwargs["env"]
    assert env["ARMED"] == "1" and env["BREAK_GLASS"] == "0"


def test_apply_armed_resolver_rules_still_fail_closed(server, monkeypatch):
    """Dropping the arm does NOT drop the resolver's own guards: a forbidden /
    unresolvable target is still refused before any subprocess."""
    monkeypatch.setattr(console_app, "_R4_ENABLED", True)
    with patch.object(console_app, "_resolve_armed_apply", return_value=(None, None, "forbidden token (gazzabyte)")), \
         patch.object(console_app, "_lane_busy", return_value=False), \
         patch("subprocess.run") as run:
        r = httpx.post(server + "/api/apply-armed", headers=H(),
                       json={"session": "cosem-tdu", "kind": "token", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 400 and "gazzabyte" in r.json()["error"]
    run.assert_not_called()


def test_queued_apply_revalidates_r4_flag_at_fire_time(monkeypatch):
    """A queued-on-busy apply never fires once the flag is pulled (the deferred
    fire honours the same gate as the immediate one)."""
    monkeypatch.delenv("CONSOLE_R4_ENABLED", raising=False)
    monkeypatch.setattr(console_app, "_APPLY_QUEUE", {"cosem-tdu:token": {"session": "cosem-tdu", "kind": "token", "status": "queued"}})
    with patch.object(console_app, "_resolve_armed_apply", return_value=(["bash", "x"], "musa", None)), \
         patch.object(console_app, "_lane_busy", return_value=False), \
         patch.object(console_app, "_fire_queued") as fire:
        console_app._apply_queue_tick()
    fire.assert_not_called()
    assert console_app._APPLY_QUEUE["cosem-tdu:token"]["status"] == "arm-expired"
    monkeypatch.setenv("CONSOLE_R4_ENABLED", "1")
    console_app._APPLY_QUEUE["cosem-tdu:token"]["status"] = "queued"
    with patch.object(console_app, "_resolve_armed_apply", return_value=(["bash", "x"], "musa", None)), \
         patch.object(console_app, "_lane_busy", return_value=False), \
         patch.object(console_app, "_fire_queued") as fire:
        console_app._apply_queue_tick()
    fire.assert_called_once()


def test_bridge_arm_helper_untouched_and_fail_closed():
    """The money/irreversible bar (_r4_current_arm over require_verified_authorization)
    is intact: no verified inbound approval -> None. The relaxation never touched it."""
    res = MagicMock(ok=False, row=None)
    with patch("scripts.lib.require_verified_authorization.verified_authorization", return_value=res):
        assert console_app._r4_current_arm() is None
    # the module's own gate still exists and is importable (not stubbed away)
    from scripts.lib import require_verified_authorization as rva
    assert callable(rva.verified_authorization)
