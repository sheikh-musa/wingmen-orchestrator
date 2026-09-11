"""Fail-closed gate: the console body may not routine-send irsyad client replies.

Governs the shared client-send tooling (reviewer_send.sh / irsyad_support_send*.sh)
so console (Nazim) cannot over-drive irsyad client-comms — coord owns those directly.
Enforce-in-code (Musa, op 19756+/19781).

DISCRIMINATOR: a lane carries an exported CC_BASE_AGENT_ID (from launch_dangerous_cc.sh),
NOT present in the shared .env; the console has none. The FIRST cut keyed on ORCH_BODY_ROLE,
which the shared .env carries as =console — so a lane sourcing .env for its DSN inherited it and
was wrongly blocked (coord regression, 2026-09-11). test_lane_with_polluted_body_role_exempt
locks that regression down.
"""
import os
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "lib" / "console_irsyad_client_send_gate.sh"

# vars the gate reads; the harness must control them exactly (this pytest session is itself
# the console body, so os.environ carries ORCH_BODY_ROLE=console — scrub every controlled var
# for any case that does not set it, or a false pass/fail slips in).
_CONTROLLED = ("CC_BASE_AGENT_ID", "ORCH_BODY_ROLE", "IRSYAD_CLIENT_SEND_OK",
               "CONSOLE_IRSYAD_CLIENT_CHANNELS")


def _run(target, **env):
    full = {k: v for k, v in os.environ.items() if k not in _CONTROLLED}
    full["ORCH_DIR"] = str(ROOT)
    full.update(env)
    cmd = f'source "{LIB}"; _console_irsyad_client_send_gate "{target}"'
    return subprocess.run(["bash", "-c", cmd], env=full, capture_output=True, text=True)


def test_console_routine_send_refused():
    # console = no CC_BASE_AGENT_ID (ORCH_BODY_ROLE=console mirrors its real env, but is not the signal)
    r = _run("gazzabyte-irsyad", ORCH_BODY_ROLE="console")
    assert r.returncode == 4, r.stderr
    assert "REFUSED" in r.stderr


def test_console_money_floor_override_allowed():
    r = _run("gazzabyte-irsyad", ORCH_BODY_ROLE="console",
             IRSYAD_CLIENT_SEND_OK="money: confirming donor create-fact to Wan")
    assert r.returncode == 0, r.stderr
    assert "OVERRIDE" in r.stderr


def test_console_money_floor_override_is_logged():
    logf = ROOT / "logs" / "console_irsyad_send_overrides.log"
    before = logf.read_text() if logf.exists() else ""
    # unique per run so the persistent log's accumulation across runs can't false-fail this
    reason = f"floor: PII gate decision, marker-{os.getpid()}-{time.time_ns()}"
    r = _run("gazzabyte-irsyad", ORCH_BODY_ROLE="console", IRSYAD_CLIENT_SEND_OK=reason)
    assert r.returncode == 0, r.stderr
    after = logf.read_text() if logf.exists() else ""
    assert reason in after and reason not in before


def test_lane_exempt():
    # coord/any lane carries CC_BASE_AGENT_ID -> must pass freely.
    r = _run("gazzabyte-irsyad", CC_BASE_AGENT_ID="cc-irsyad-coord")
    assert r.returncode == 0, r.stderr


def test_lane_with_polluted_body_role_exempt():
    # THE 2026-09-11 REGRESSION: a lane sourced the shared .env (→ ORCH_BODY_ROLE=console) for its
    # DSN, but still carries its exported CC_BASE_AGENT_ID. Must be EXEMPT, not blocked.
    r = _run("gazzabyte-irsyad", CC_BASE_AGENT_ID="cc-irsyad-coord", ORCH_BODY_ROLE="console")
    assert r.returncode == 0, r.stderr


def test_console_other_channel_unaffected():
    # a non-irsyad channel console legitimately owns (e.g. cosem-exams via reviewer_send)
    r = _run("cosem-exams", ORCH_BODY_ROLE="console")
    assert r.returncode == 0, r.stderr


def test_extensible_channel_set():
    # a second irsyad client surface, added via the env list, is also gated for the console.
    r = _run("irsyad-support-2", ORCH_BODY_ROLE="console",
             CONSOLE_IRSYAD_CLIENT_CHANNELS="gazzabyte-irsyad irsyad-support-2")
    assert r.returncode == 4, r.stderr


def test_lane_exempt_on_extended_channel():
    r = _run("irsyad-support-2", CC_BASE_AGENT_ID="cc-irsyad",
             CONSOLE_IRSYAD_CLIENT_CHANNELS="gazzabyte-irsyad irsyad-support-2")
    assert r.returncode == 0, r.stderr
