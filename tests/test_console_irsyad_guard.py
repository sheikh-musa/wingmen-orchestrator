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


# ---------------------------------------------------------------------------
# bus #62942: the console MUST be able to bus-post a gate RULING that names the
# irsyad store to cc-irsyad-coord (coord's exec_prod validator, LAYER-VOCAB-001,
# requires the store ref). Narrow, FAIL-CLOSED allow-exception: ONLY a console
# Bash call that is exactly a `scripts/bus_send.py --to cc-irsyad-coord[-N]` post
# with benign flags, no shell chaining, no command substitution/redirect, no
# second subprocess. Everything else stays blocked.
# ---------------------------------------------------------------------------
def _bash(command):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def test_console_bus_post_to_coord_with_store_ref_is_allowed(monkeypatch):
    cmd = (
        '.venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord '
        '--subject "PR#1086 gate ruling" '
        '--body "ACCEPT conditional: prove goumlynecruxrlmzlntp store leak-guard by construction"'
    )
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 0


def test_console_bus_post_to_coord_instance_variant_is_allowed(monkeypatch):
    cmd = (
        'python3 scripts/bus_send.py --to cc-irsyad-coord-1 '
        '--body "mig452 GO: GOUMLYNE_ store, leak-guard proven" --requires-response'
    )
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 0


def test_console_bus_post_to_coord_chained_psql_is_blocked(monkeypatch, capsys):
    cmd = (
        '.venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --body "x" '
        '; psql goumlynecruxrlmzlntp'
    )
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 2


def test_console_bus_post_to_coord_command_substitution_is_blocked(monkeypatch):
    cmd = (
        '.venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord '
        '--body "$(psql goumlynecruxrlmzlntp -c \'select 1\')"'
    )
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 2


def test_console_bus_post_to_other_agent_with_alias_is_blocked(monkeypatch):
    cmd = '.venv/bin/python3 scripts/bus_send.py --to cc-irsyad-1 --body "GOUMLYNE_ store"'
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 2


def test_console_non_bash_tool_touching_goumlyne_still_blocked(monkeypatch):
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x/GOUMLYNE_env"}})
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=payload)
    assert rc == 2


def test_console_bash_non_bussend_touching_goumlyne_still_blocked(monkeypatch):
    # A Bash call that is NOT a bus_send to coord must stay blocked even if it names coord.
    cmd = "psql goumlynecruxrlmzlntp  # posting for cc-irsyad-coord"
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 2


# ---------------------------------------------------------------------------
# PR#370 review: the first cut was fail-OPEN on 5 vectors (plain shlex.split keeps a
# GLUED operator as one token; newline check was per-token; program/script match was
# basename-only). The helper must now reject each. These assert the helper DIRECTLY
# (the real security property) AND via main() with a silo token (rc 2 = correctly
# falls through to the BLOCKED silo check instead of being allowed).
# ---------------------------------------------------------------------------
_BYPASS_EXACT = [  # the reviewer's exact cases + glued operator variants
    "python3 scripts/bus_send.py --to cc-irsyad-coord --subject x;whoami",        # glued ;
    "python3 scripts/bus_send.py --to cc-irsyad-coord --subject a&&whoami",        # glued &&
    "python3 scripts/bus_send.py --to cc-irsyad-coord --subject x\nwhoami",        # literal newline
    "python3 /tmp/x/bus_send.py --to cc-irsyad-coord",                             # any bus_send.py
    "/tmp/python3 scripts/bus_send.py --to cc-irsyad-coord",                       # any python3
    "python3 scripts/bus_send.py --to cc-irsyad-coord --subject x|y",             # glued |
    "python3 scripts/bus_send.py --to cc-irsyad-coord --subject x>f",             # glued >
    "python3 scripts/bus_send.py --to cc-irsyad-coord --subject x<f",             # glued <
    "python3 scripts/bus_send.py --to cc-irsyad-coord --subject x&",              # glued &
]


@pytest.mark.parametrize("cmd", _BYPASS_EXACT)
def test_allow_helper_rejects_each_bypass_directly(cmd):
    assert guard._is_allowed_coord_bus_post({"tool_name": "Bash", "tool_input": {"command": cmd}}) is False


def test_allow_helper_accepts_legit_coord_post_directly():
    cmd = (
        ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord "
        '--body "ACCEPT: goumlynecruxrlmzlntp store leak-guard proven"'
    )
    assert guard._is_allowed_coord_bus_post({"tool_name": "Bash", "tool_input": {"command": cmd}}) is True
    cmd1 = "python3 scripts/bus_send.py --to cc-irsyad-coord-1 --body-file /tmp/ruling.txt"
    assert guard._is_allowed_coord_bus_post({"tool_name": "Bash", "tool_input": {"command": cmd1}}) is True


# Same vectors carrying a silo token -> main() must return 2 (helper rejects -> BLOCKED
# silo check fires), i.e. the bypass can no longer smuggle a goumlyne-touching command.
_BYPASS_WITH_SILO = [
    ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --subject goumlynecruxrlmzlntp;whoami",
    ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --subject goumlynecruxrlmzlntp&&whoami",
    ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --subject goumlynecruxrlmzlntp\nwhoami",
    ".venv/bin/python3 /tmp/x/bus_send.py --to cc-irsyad-coord --body goumlynecruxrlmzlntp",
    "/tmp/python3 scripts/bus_send.py --to cc-irsyad-coord --body goumlynecruxrlmzlntp",
    ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --subject goumlynecruxrlmzlntp|whoami",
    ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --subject goumlynecruxrlmzlntp>f",
    ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --subject goumlynecruxrlmzlntp<f",
    ".venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --subject goumlynecruxrlmzlntp&",
]


@pytest.mark.parametrize("cmd", _BYPASS_WITH_SILO)
def test_console_bypass_with_silo_token_is_blocked_via_main(monkeypatch, cmd):
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 2
