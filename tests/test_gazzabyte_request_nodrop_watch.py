"""No-drop watchdog for Gazzabyte requests (Musa op19790).

Tests the pure evaluate() decision core: coord gets nudged when the client is left
waiting; console is the backstop only when coord already had a cycle; the operator is
NEVER a target; answered/fresh channels produce no noise; dedup holds.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "nodrop", ROOT / "scripts" / "gazzabyte_request_nodrop_watch.py")
nodrop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nodrop)

NUDGE = 3 * 3600
ESC = 12 * 3600
RENUDGE = 4 * 3600
NOW = 1_000_000.0


def _inbound(age_s, mid=101):
    return {"id": mid, "direction": "inbound", "created_epoch": NOW - age_s,
            "from_name": "Wan", "text": "please add the export column"}


def _outbound(age_s, mid=102):
    return {"id": mid, "direction": "outbound", "created_epoch": NOW - age_s,
            "from_name": None, "text": "on it"}


def ev(latest, state):
    return nodrop.evaluate(latest, NOW, state, NUDGE, ESC, RENUDGE)


def test_client_waiting_past_window_nudges_coord():
    actions, st = ev(_inbound(NUDGE + 60), {})
    assert len(actions) == 1
    assert actions[0]["to"] == "cc-irsyad-coord"
    assert st["101"]["coord_nudge_count"] == 1


def test_answered_channel_no_nudge_and_clears_state():
    actions, st = ev(_outbound(NUDGE + 999), {"101": {"coord_nudged_at": 1}})
    assert actions == []
    assert st == {}  # tracking cleared once someone replied last


def test_fresh_inbound_no_nudge():
    actions, st = ev(_inbound(NUDGE - 60), {})
    assert actions == []


def test_empty_channel_no_nudge():
    actions, st = ev(None, {})
    assert actions == [] and st == {}


def test_coord_nudge_deduped_within_backoff():
    # coord nudged 1h ago (< RENUDGE 4h) -> no re-nudge this cycle
    state = {"101": {"coord_nudged_at": NOW - 3600, "coord_nudge_count": 1}}
    actions, st = ev(_inbound(NUDGE + 3600), state)
    assert actions == []


def test_console_backstop_only_after_prior_coord_cycle():
    # aged past ESCALATE, coord was nudged on a PRIOR cycle (long enough ago to re-fire) -> escalate
    state = {"101": {"coord_nudged_at": NOW - (RENUDGE + 60), "coord_nudge_count": 1}}
    actions, st = ev(_inbound(ESC + 60), state)
    tos = {a["to"] for a in actions}
    assert "cc-irsyad-coord" in tos  # re-nudge coord (backoff elapsed)
    assert "orch-console" in tos     # AND console backstop
    assert st["101"]["console_escalated_at"] == NOW


def test_no_console_escalation_on_first_ever_cycle():
    # even a very old drop discovered fresh gives coord a cycle first (no prior_coord) -> no console
    actions, st = ev(_inbound(ESC + 5000), {})
    tos = {a["to"] for a in actions}
    assert tos == {"cc-irsyad-coord"}


def test_operator_is_never_a_target():
    # across every escalation state, no action ever targets an operator/musa address
    for state in ({}, {"101": {"coord_nudged_at": NOW - (RENUDGE + 60), "coord_nudge_count": 3}}):
        actions, _ = ev(_inbound(ESC + 9999), state)
        for a in actions:
            assert a["to"] in ("cc-irsyad-coord", "orch-console")
