"""Unit tests for scripts/hooks/console_irsyad_guard.py -- the PreToolUse guard that
keeps the console body (Nazim) off irsyad work (Musa op#21944/#22229, enforce-in-code).

bus #44525: a malformed/unreadable stdin payload must fail CLOSED for the console body
(refuse the tool call) since a fail-open here would let exactly the class of tool call
the guard exists to block through silently. Non-console bodies (lanes, incl. coord) are
exempt from the guard entirely and must stay fail-open."""
import importlib.util
import io
import json
import pathlib

import pytest

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "console_irsyad_guard.py"
_spec = importlib.util.spec_from_file_location("console_irsyad_guard", _MOD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _run(monkeypatch, *, env, stdin_text):
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO(stdin_text))
    return guard.main()


def test_console_with_malformed_stdin_is_refused(monkeypatch, capsys):
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text="not json")
    assert rc == 2
    assert "refusing" in capsys.readouterr().err


def test_console_with_empty_stdin_is_refused(monkeypatch, capsys):
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text="")
    assert rc == 2
    assert "refusing" in capsys.readouterr().err


def test_non_console_with_malformed_stdin_passes(monkeypatch):
    # ORCH_BODY_ROLE unset entirely -> not console -> exempt before stdin is even read
    rc = _run(monkeypatch, env={}, stdin_text="not json")
    assert rc == 0


def test_lane_with_cc_base_agent_id_is_exempt_even_with_console_role(monkeypatch):
    rc = _run(
        monkeypatch,
        env={"ORCH_BODY_ROLE": "console", "CC_BASE_AGENT_ID": "cc-irsyad-coord"},
        stdin_text="not json",
    )
    assert rc == 0


def test_console_with_valid_non_matching_payload_still_passes(monkeypatch):
    payload = json.dumps({"tool_input": {"command": "ls -la"}})
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=payload)
    assert rc == 0


def test_console_touching_goumlyne_is_blocked(monkeypatch, capsys):
    payload = json.dumps({"tool_input": {"command": "psql goumlynecruxrlmzlntp"}})
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=payload)
    assert rc == 2
    assert "irsyad silo (goumlyne)" in capsys.readouterr().err


@pytest.mark.parametrize("role_env", [{}, {"ORCH_BODY_ROLE": "hub"}])
def test_non_console_touching_goumlyne_passes(monkeypatch, role_env):
    payload = json.dumps({"tool_input": {"command": "psql goumlynecruxrlmzlntp"}})
    rc = _run(monkeypatch, env=role_env, stdin_text=payload)
    assert rc == 0
