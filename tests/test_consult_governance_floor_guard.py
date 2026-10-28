"""Unit tests + benchmark for scripts/hooks/consult_governance_floor_guard.py.

The guard enforces CONSULT-BEFORE-ASSERT: an agent about to assert a governance floor /
sign-off authority must have read docs/governance-floors.md (the SSOT) in this session,
or the call is surfaced with a reminder pointing at it.

The BENCHMARK (bottom of file) replays the 2026-10-15 incident: the console asserting
"irsyad money go-live needs cai+Nazim" WITHOUT consulting the record. It proves the hook
fires on that assertion and points at the SSOT BEFORE the wrong assertion can leave the
session — and that reading the SSOT first clears the gate.
"""
import importlib.util
import io
import json
import pathlib

import pytest

_MOD_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts" / "hooks" / "consult_governance_floor_guard.py"
)
_spec = importlib.util.spec_from_file_location("consult_governance_floor_guard", _MOD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _run(monkeypatch, tmp_path, *, payload, mode=None, stdin_override=None):
    """Run the hook with an isolated per-test state dir so markers don't leak between tests."""
    monkeypatch.delenv("GOV_FLOOR_GUARD_MODE", raising=False)
    monkeypatch.setenv("GOV_FLOOR_GUARD_STATE_DIR", str(tmp_path))
    # CONSOLE-ONLY rollout (op#30353): the hook gates only the console body, so the tests
    # must run as the console (ORCH_BODY_ROLE=console, no CC_BASE_AGENT_ID) to exercise it.
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    if mode is not None:
        monkeypatch.setenv("GOV_FLOOR_GUARD_MODE", mode)
    text = stdin_override if stdin_override is not None else json.dumps(payload)
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(text))
    return guard.main()


def _send(cmd, session="s1"):
    return {"session_id": session, "tool_name": "Bash", "tool_input": {"command": cmd}}


def _read_ssot(session="s1"):
    return {
        "session_id": session,
        "tool_name": "Read",
        "tool_input": {"file_path": "/repo/docs/governance-floors.md"},
    }


# ── mode: off ───────────────────────────────────────────────────────────────
def test_off_mode_is_noop(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path, payload=_send('tg_send.sh "go-live floor is cai+Nazim"'), mode="off")
    assert rc == 0


# ── non-assertions pass ───────────────────────────────────────────────────────
def test_benign_command_passes(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path, payload=_send("ls -la"))
    assert rc == 0


def test_governance_chatter_without_floor_language_passes(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path, payload=_send('tg_send.sh "deploying ihsanos now"'))
    assert rc == 0


# ── console-only rollout (op#30353): non-console bodies pass through ───────────
def test_non_console_body_passes_through(monkeypatch, tmp_path):
    # A lane / coord (ORCH_BODY_ROLE unset OR CC_BASE_AGENT_ID set) is NOT gated in the
    # console-only rollout, even on a bare floor assertion with no consult.
    monkeypatch.setenv("GOV_FLOOR_GUARD_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("GOV_FLOOR_GUARD_MODE", raising=False)
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)   # a lane
    monkeypatch.setattr(
        guard.sys, "stdin",
        io.StringIO(json.dumps(_send('bus_send.py --body "the go-live floor needs cai+Nazim"'))),
    )
    assert guard.main() == 0
    # also: a coord instance carries CC_BASE_AGENT_ID -> not console -> pass
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-irsyad-coord")
    monkeypatch.setattr(
        guard.sys, "stdin",
        io.StringIO(json.dumps(_send('bus_send.py --body "owner-sign is cai+Nazim"'))),
    )
    assert guard.main() == 0


# ── gate mode (default): assertion without consult is refused ─────────────────
def test_floor_assertion_without_consult_is_blocked(monkeypatch, tmp_path, capsys):
    rc = _run(monkeypatch, tmp_path, payload=_send('nazim_send.sh "the go-live floor needs cai+Nazim"'))
    assert rc == 2
    err = capsys.readouterr().err
    assert "docs/governance-floors.md" in err
    assert "CONSULT-BEFORE-ASSERT" in err


def test_money_sign_assertion_blocked(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path, payload=_send('bus_send.py --body "money-sign is required here"'))
    assert rc == 2


def test_owner_sign_assertion_blocked(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path, payload=_send('tg_send.sh "owner-sign must come from the operator"'))
    assert rc == 2


def test_assertion_in_write_content_blocked(monkeypatch, tmp_path):
    payload = {
        "session_id": "s1",
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/handoff.md", "content": "go-live floor: requires cai"},
    }
    rc = _run(monkeypatch, tmp_path, payload=payload)
    assert rc == 2


# ── consultation clears the gate ──────────────────────────────────────────────
def test_reading_ssot_then_asserting_passes(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, payload=_read_ssot()) == 0  # consult
    rc = _run(monkeypatch, tmp_path, payload=_send('nazim_send.sh "the go-live floor is console + Shuq/Wan"'))
    assert rc == 0


def test_bash_read_of_ssot_counts_as_consult(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, payload=_send("cat docs/governance-floors.md")) == 0  # consult via bash
    rc = _run(monkeypatch, tmp_path, payload=_send('tg_send.sh "money-sign path confirmed"'))
    assert rc == 0


def test_authoring_the_ssot_is_consult_not_assertion(monkeypatch, tmp_path):
    # Writing the SSOT itself contains floor language but must NOT be treated as an assertion.
    payload = {
        "session_id": "s1",
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/docs/governance-floors.md", "content": "go-live floor ... money-sign"},
    }
    assert _run(monkeypatch, tmp_path, payload=payload) == 0


def test_consult_is_session_scoped(monkeypatch, tmp_path):
    # Consult in session s1 does NOT clear the gate for a different session s2.
    assert _run(monkeypatch, tmp_path, payload=_read_ssot(session="s1")) == 0
    rc = _run(monkeypatch, tmp_path, payload=_send('tg_send.sh "go-live floor needs cai+Nazim"', session="s2"))
    assert rc == 2


# ── warn mode: never blocks, still nudges ─────────────────────────────────────
def test_warn_mode_does_not_block_but_nudges(monkeypatch, tmp_path, capsys):
    rc = _run(monkeypatch, tmp_path, payload=_send('nazim_send.sh "go-live floor needs cai+Nazim"'), mode="warn")
    assert rc == 0
    assert "docs/governance-floors.md" in capsys.readouterr().err


# ── fail-closed on unreadable stdin in gate mode ─────────────────────────────
def test_gate_mode_fails_closed_on_bad_stdin(monkeypatch, tmp_path, capsys):
    rc = _run(monkeypatch, tmp_path, payload=None, mode="gate", stdin_override="not json")
    assert rc == 2
    assert "fail-closed" in capsys.readouterr().err


def test_warn_mode_fails_open_on_bad_stdin(monkeypatch, tmp_path):
    rc = _run(monkeypatch, tmp_path, payload=None, mode="warn", stdin_override="not json")
    assert rc == 0


# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK — replay the 2026-10-15 incident the SSOT + hook exist to prevent.
# ══════════════════════════════════════════════════════════════════════════════
class TestIncidentBenchmark:
    """Would the mechanism have surfaced docs/governance-floors.md BEFORE the wrong
    'cai+Nazim' irsyad assertion? Yes."""

    WRONG_ASSERTION = 'nazim_send.sh "irsyad money go-live needs cai+Nazim to sign"'
    CORRECT_ASSERTION = (
        'nazim_send.sh "irsyad go-live floor = console safety-floor + Shuq/Wan owner-sign; cai OFF"'
    )

    def test_wrong_assertion_is_intercepted_and_points_to_ssot(self, monkeypatch, tmp_path, capsys):
        # The console tries to send the STALE floor WITHOUT having consulted the record.
        rc = _run(monkeypatch, tmp_path, payload=_send(self.WRONG_ASSERTION))
        err = capsys.readouterr().err
        # The hook fires (the stale assertion cannot leave the session) ...
        assert rc == 2
        # ... and it points the agent at the authoritative source BEFORE the wrong answer ships.
        assert "docs/governance-floors.md" in err
        assert "SINGLE SOURCE OF TRUTH" in err

    def test_after_consulting_the_correct_floor_ships(self, monkeypatch, tmp_path):
        # Having been pointed at the SSOT, the agent reads it (consult) ...
        assert _run(monkeypatch, tmp_path, payload=_read_ssot()) == 0
        # ... and now the CORRECT irsyad floor assertion passes unimpeded.
        rc = _run(monkeypatch, tmp_path, payload=_send(self.CORRECT_ASSERTION))
        assert rc == 0

    def test_ssot_actually_contains_the_correct_irsyad_floor(self):
        # The record the hook points at must hold the right answer, not just exist.
        ssot = pathlib.Path(_MOD_PATH).resolve().parents[2] / "docs" / "governance-floors.md"
        body = ssot.read_text()
        assert "Shuq (SMS) / Wan (DMS)" in body
        assert "cai is OUT of irsyad" in body
        # and explicitly flags the stale phrase as wrong for irsyad
        assert "cai+Nazim" in body and "STALE / WRONG" in body


# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK 2 — replay the 2026-10-27 relapse: HOLDING an irsyad action on a Musa
# sign-off is a floor-assertion too, and must trip the consult-gate.
# ══════════════════════════════════════════════════════════════════════════════
class TestHoldIsAFloorAssertionBenchmark:
    """The console held the irsyad backfill for hours on a Musa 'YES BACKFILL' verified
    grant that the SSOT says does not exist (owners Shuq/Wan are sole sign-off). A HOLD
    asserts a floor; the gate must force the SSOT read before the hold ships."""

    # The shapes the relapse actually took — a held_commitment / bus / operator line.
    HOLD_SHAPES = [
        'nazim_send.sh "holding the irsyad backfill pending your YES BACKFILL grant"',
        'bus_send.py --body "GO withheld — awaiting Musa verified authorization on the backfill"',
        'python3 -c "find_verified_authorization(chat_id=MUSA, phrases=[\'YES BACKFILL\'])"',
        'bus_send.py --body "irsyad money-path hold until explicit grant"',
    ]

    @pytest.mark.parametrize("cmd", HOLD_SHAPES)
    def test_hold_assertion_without_consult_is_blocked(self, monkeypatch, tmp_path, capsys, cmd):
        rc = _run(monkeypatch, tmp_path, payload=_send(cmd))
        assert rc == 2, f"hold-shaped assertion should trip the gate: {cmd!r}"
        assert "docs/governance-floors.md" in capsys.readouterr().err

    def test_after_consult_hold_language_passes(self, monkeypatch, tmp_path):
        # Once the SSOT is read, the gate clears — the agent can correctly state the
        # withdrawn hold ("nothing waits on Musa") without being blocked again.
        assert _run(monkeypatch, tmp_path, payload=_read_ssot()) == 0
        rc = _run(
            monkeypatch, tmp_path,
            payload=_send('nazim_send.sh "nothing waits on you — hold on your sign-off withdrawn"'),
        )
        assert rc == 0

    def test_benign_backfill_mention_without_hold_language_passes(self, monkeypatch, tmp_path):
        # Don't over-fire: a plain status line about a backfill with no hold/authorization
        # shape must pass even unconsulted.
        rc = _run(monkeypatch, tmp_path, payload=_send('bus_send.py --body "backfill report 3 of 7 written and verified"'))
        assert rc == 0
