"""Fail-closed gate: the console body may not routine-send irsyad client replies.

Governs the shared client-send tooling (reviewer_send.sh / irsyad_support_send*.sh)
so console (Nazim) cannot over-drive irsyad client-comms — coord owns those directly.
Enforce-in-code (Musa, op 19756+/19781). Critical safety property under test:
the gate NEVER fires for coord (no ORCH_BODY_ROLE=console), only for the console body.
"""
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "lib" / "console_irsyad_client_send_gate.sh"

# vars the gate reads; the harness must control them exactly (this pytest session
# is itself the console body, so os.environ carries ORCH_BODY_ROLE=console — scrub it
# for any case that does not set it, or the coord-safety test would be a false pass).
_CONTROLLED = ("ORCH_BODY_ROLE", "IRSYAD_CLIENT_SEND_OK", "CONSOLE_IRSYAD_CLIENT_CHANNELS")


def _run(target, **env):
    full = {k: v for k, v in os.environ.items() if k not in _CONTROLLED}
    full["ORCH_DIR"] = str(ROOT)
    full.update(env)
    cmd = f'source "{LIB}"; _console_irsyad_client_send_gate "{target}"'
    return subprocess.run(["bash", "-c", cmd], env=full, capture_output=True, text=True)


def test_console_routine_send_refused():
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
    reason = "floor: PII gate decision, unique-marker-42"
    r = _run("gazzabyte-irsyad", ORCH_BODY_ROLE="console", IRSYAD_CLIENT_SEND_OK=reason)
    assert r.returncode == 0, r.stderr
    after = logf.read_text() if logf.exists() else ""
    assert reason in after and reason not in before


def test_coord_body_unaffected():
    # coord carries NO ORCH_BODY_ROLE=console -> must pass freely (the safety property).
    r = _run("gazzabyte-irsyad")
    assert r.returncode == 0, r.stderr


def test_hub_body_unaffected():
    r = _run("gazzabyte-irsyad", ORCH_BODY_ROLE="hub")
    assert r.returncode == 0, r.stderr


def test_console_other_channel_unaffected():
    # a non-irsyad channel console legitimately owns (e.g. cosem-exams via reviewer_send)
    r = _run("cosem-exams", ORCH_BODY_ROLE="console")
    assert r.returncode == 0, r.stderr


def test_extensible_channel_set():
    # a second irsyad client surface, added via the env list, is also gated.
    r = _run("irsyad-support-2", ORCH_BODY_ROLE="console",
             CONSOLE_IRSYAD_CLIENT_CHANNELS="gazzabyte-irsyad irsyad-support-2")
    assert r.returncode == 4, r.stderr
