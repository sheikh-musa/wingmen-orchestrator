"""op#22669 --ask/--chase-hours flag wiring in scripts/tg_send.sh and
scripts/nazim_send.sh. ORCH_DIR is hardcoded to $HOME/wingmen/orchestrator in
both scripts (not relative to the script under test), and running either one
for real would hit the live Telegram API — so this test extracts the EXACT
shipped flag-parsing block (verbatim, by anchor strings) and behaviorally
exercises it in isolation via a wrapper harness, instead of only regex-
matching for its presence (which could pass on a subtly broken copy)."""
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
_TG_SEND = _SCRIPTS / "tg_send.sh"
_NAZIM_SEND = _SCRIPTS / "nazim_send.sh"

_START = 'ASK=""'
_END = 'TEXT="${1:-$(cat)}"'


def _extract_parse_block(path: Path) -> str:
    text = path.read_text()
    i = text.index(_START)
    j = text.index(_END, i)
    return text[i:j]


def _run(block: str, argv: list) -> subprocess.CompletedProcess:
    harness = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        + block
        + '\necho "ASK=[$ASK]"\n'
          'echo "CHASE=[$CHASE_HOURS]"\n'
          'echo "REST=[$*]"\n'
    )
    return subprocess.run(
        ["bash", "-c", harness, "--"] + argv,
        capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_scripts_exist(path):
    assert path.is_file(), f"missing {path}"


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_syntax_is_valid_bash(path):
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_no_flags_leaves_ask_and_chase_empty_and_preserves_message(path):
    block = _extract_parse_block(path)
    r = _run(block, ["hello there"])
    assert r.returncode == 0, r.stderr
    assert "ASK=[]" in r.stdout
    assert "CHASE=[]" in r.stdout
    assert "REST=[hello there]" in r.stdout


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_ask_flag_is_captured_and_stripped_from_positional(path):
    block = _extract_parse_block(path)
    r = _run(block, ["--ask", "approve the migration", "the message text"])
    assert r.returncode == 0, r.stderr
    assert "ASK=[approve the migration]" in r.stdout
    assert "REST=[the message text]" in r.stdout


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_chase_hours_flag_is_captured_and_stripped(path):
    block = _extract_parse_block(path)
    r = _run(block, ["--chase-hours", "6", "--ask", "go ahead?", "msg"])
    assert r.returncode == 0, r.stderr
    assert "ASK=[go ahead?]" in r.stdout
    assert "CHASE=[6]" in r.stdout
    assert "REST=[msg]" in r.stdout


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_flags_after_the_message_are_still_captured(path):
    # tag/message positionals can precede the flags on some call shapes.
    block = _extract_parse_block(path)
    r = _run(block, ["msg", "--ask", "an ask"])
    assert r.returncode == 0, r.stderr
    assert "ASK=[an ask]" in r.stdout
    assert "REST=[msg]" in r.stdout


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_empty_positional_array_does_not_error_under_set_dash_u(path):
    # the exact bug class this guard exists for: expanding an EMPTY bash array
    # under `set -u` raises "unbound variable" without the length-guard.
    block = _extract_parse_block(path)
    r = _run(block, ["--ask", "only a flag, no message"])
    assert r.returncode == 0, r.stderr
    assert "unbound variable" not in r.stderr
    assert "REST=[]" in r.stdout


# ── the guarded --ask dispatch: verify the invocation shape statically ───────
# (behaviorally running THIS half would hit the live Telegram/asks_open.py
# path against the real substrate — out of scope for a unit test; see the
# module docstring.)
def test_tg_send_asks_open_call_passes_outbound_msg_id_no_forced_delegate():
    code = _TG_SEND.read_text()
    assert 'asks_open.py" "$ASK"' in code
    assert '--outbound-msg-id "$OPLOGID"' in code
    # the hub lets asks_open.py default delegated_to (ORCH_AGENT_ID/orch-console) —
    # it must not hardcode a --delegated-to the way the console script does.
    assert '--delegated-to orch-console' not in code


def test_nazim_send_asks_open_call_forces_delegate_to_console():
    code = _NAZIM_SEND.read_text()
    assert 'asks_open.py" "$ASK"' in code
    assert '--delegated-to orch-console' in code


@pytest.mark.parametrize("path", [_TG_SEND, _NAZIM_SEND], ids=["tg_send.sh", "nazim_send.sh"])
def test_ask_dispatch_is_gated_on_sent_eq_1(path):
    code = path.read_text()
    assert 'if [ -n "$ASK" ] && [ "$sent" = 1 ]; then' in code, (
        "an --ask must never open a ledger row for a message that failed to send"
    )
