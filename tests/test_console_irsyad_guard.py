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
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)  # clean slate for the provider-routed discriminator
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


# The canonical allowed shape — REAL bus_send.py flags (PR#370 follow-up): --type +
# --priority required, body via --body, --req (not --requires-response). The drift test
# below asserts this parses on bus_send.build_parser(), so guard/tool can't drift.
_CANON_BODY = "mig452 ACCEPT: goumlynecruxrlmzlntp store leak-guard proven by construction"
_CANON_CMD = (
    '.venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --type decision '
    f'--subject "mig452 ruling" --priority P1 --body "{_CANON_BODY}"'
)
_CANON_CMD_INSTANCE = (
    'python3 scripts/bus_send.py --to cc-irsyad-coord-1 --type decision '
    f'--subject "mig452 ruling" --priority P1 --req --body "{_CANON_BODY}"'
)


def test_console_canonical_coord_post_is_allowed(monkeypatch):
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(_CANON_CMD))
    assert rc == 0


def test_console_canonical_coord_post_instance_variant_is_allowed(monkeypatch):
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(_CANON_CMD_INSTANCE))
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


def test_console_bus_post_body_file_is_blocked_exfil(monkeypatch):
    # --body-file would let the console post ANY local file's contents (e.g. .env secrets)
    # into a coord bus row -> secrets-exfil through the coord-post exception. The console's
    # coord rulings are short inline --body text; --body-file is NOT allowlisted and is
    # actively BLOCKED. Note: removing it from the allowlist alone is INSUFFICIENT (the guard
    # is default-allow and the path carries no silo token) -- it must hit a BLOCKED pattern.
    for target in (".env", "/Users/sheikhmusa/wingmen/orchestrator/.env"):
        cmd = (
            "python3 scripts/bus_send.py --to cc-irsyad-coord --type decision "
            f"--subject r --priority P1 --body-file {target}"
        )
        rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
        assert rc == 2, target


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
    # cc-irsyad-coord-1 instance variant with an inline --body is also accepted:
    cmd1 = 'python3 scripts/bus_send.py --to cc-irsyad-coord-1 --body "ACCEPT: goumlynecruxrlmzlntp store ruling proven"'
    assert guard._is_allowed_coord_bus_post({"tool_name": "Bash", "tool_input": {"command": cmd1}}) is True
    # --body-file is NOT accepted by the helper (secrets-exfil path; bus #63261) -- the
    # console posts short inline --body rulings, never a file; main() then BLOCKS it.
    cmd2 = "python3 scripts/bus_send.py --to cc-irsyad-coord-1 --body-file /tmp/ruling.txt"
    assert guard._is_allowed_coord_bus_post({"tool_name": "Bash", "tool_input": {"command": cmd2}}) is False


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


# ---------------------------------------------------------------------------
# DRIFT-PROOF (PR#370 follow-up): the guard's allowed shape must ACTUALLY parse on
# the REAL scripts/bus_send.py. The first cut allowlisted a fictional CLI (--body
# didn't exist; --requires-response/--from not real), so a real coord post could not
# pass the guard. Import the real tool and assert the canonical allowed shape parses
# + its body resolves -> this test fails loudly if guard and tool ever drift again.
# ---------------------------------------------------------------------------
import shlex as _shlex  # noqa: E402
import sys as _sys  # noqa: E402

_sys.path.insert(0, str(_MOD_PATH.parents[2] / "scripts"))
import bus_send as _bus  # noqa: E402


def test_canonical_allowed_shape_parses_on_real_bus_send():
    argv = _shlex.split(_CANON_CMD)[2:] + ["--dry-run"]  # drop interpreter + script path
    args = _bus.build_parser().parse_args(argv)  # must NOT raise ("unrecognized arguments")
    assert args.to == "cc-irsyad-coord"
    assert args.type == "decision"
    assert args.priority == "P1"
    assert _bus.resolve_body(args, io.StringIO("")) == _CANON_BODY  # --body resolves, >=40B


def test_canonical_instance_shape_parses_on_real_bus_send():
    argv = _shlex.split(_CANON_CMD_INSTANCE)[2:] + ["--dry-run"]
    args = _bus.build_parser().parse_args(argv)
    assert args.to == "cc-irsyad-coord-1"
    assert args.req is True
    assert _bus.resolve_body(args, io.StringIO("")) == _CANON_BODY


def test_console_coord_post_with_from_flag_is_blocked(monkeypatch):
    # --from is NOT allowlisted (console must not post under another identity); the
    # store ref in the body then falls through to the BLOCKED silo check -> rc 2.
    cmd = (
        '.venv/bin/python3 scripts/bus_send.py --to cc-irsyad-coord --type decision '
        f'--subject "x" --priority P1 --from cc-orchestrator --body "{_CANON_BODY}"'
    )
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash(cmd))
    assert rc == 2


# ── CAI-1225 belt (2026-10-14): the guard ALSO fires for a PROVIDER-ROUTED body ──
# A non-Anthropic-routed cai or GLM lane must stay off the goumlyne/ihsanos client silos
# even without the console role — it inferences on a third-party endpoint.
_ZAI = {"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic"}


def _bash(cmd):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})


def test_provider_routed_blocks_silo_cred(monkeypatch, capsys):
    rc = _run(monkeypatch, env=dict(_ZAI), stdin_text=_bash('psql "$GOUMLYNE_DATABASE_URL" -c "select 1"'))
    assert rc == 2
    assert "goumlyne" in capsys.readouterr().err.lower()


def test_provider_routed_blocks_write_dsn_env(monkeypatch, capsys):
    rc = _run(monkeypatch, env=dict(_ZAI), stdin_text=_bash("cat ~/.wingmen/private/write_dsn.env"))
    assert rc == 2
    assert "client-silo write dsn" in capsys.readouterr().err.lower()


def test_console_still_blocks_silo_cred(monkeypatch, capsys):
    # the original console trigger is unchanged
    rc = _run(monkeypatch, env={"ORCH_BODY_ROLE": "console"}, stdin_text=_bash('psql "$IHSANOS_PROD_DATABASE_URL" -c "select 1"'))
    assert rc == 2
    assert "ceayj" in capsys.readouterr().err.lower()


def test_provider_routed_benign_cmd_allowed(monkeypatch):
    # guard applies (routed) but nothing matches a silo pattern -> allow
    rc = _run(monkeypatch, env=dict(_ZAI), stdin_text=_bash("echo hi && pwd"))
    assert rc == 0


def test_plain_anthropic_lane_is_exempt(monkeypatch):
    # not console, not provider-routed -> exempt, even for a silo-cred command
    rc = _run(monkeypatch, env={}, stdin_text=_bash('psql "$GOUMLYNE_DATABASE_URL" -c "select 1"'))
    assert rc == 0


def test_anthropic_host_base_url_is_not_routed(monkeypatch):
    # an anthropic.com base URL is NOT provider-routed -> exempt
    rc = _run(monkeypatch, env={"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
              stdin_text=_bash('cat ~/.wingmen/private/write_dsn.env'))
    assert rc == 0
