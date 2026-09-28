"""--secret-vault-key flag wiring in scripts/tg_send.sh, scripts/nazim_send.sh,
and scripts/oeh_send.sh (bus #44378, after op#22696 + the 2026-09-27 OEH
preview-password leak into operator_messages row 22824).

Same approach as tests/test_send_scripts_ask_flag.py: ORCH_DIR is hardcoded
to $HOME/wingmen/orchestrator in all three scripts and running any of them
for real would hit the live Telegram API and vault, so (1) the exact shipped
flag-parsing block is extracted (verbatim, by anchor strings) and
behaviorally exercised in isolation, and (2) the send/log wiring downstream
of it — which text variable feeds the Telegram send vs the durable log — is
verified by static assertion against the shipped source, the same technique
already used by test_tg_send_asks_open_call_passes_outbound_msg_id_no_forced_delegate.
"""
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
_TG_SEND = _SCRIPTS / "tg_send.sh"
_NAZIM_SEND = _SCRIPTS / "nazim_send.sh"
_OEH_SEND = _SCRIPTS / "oeh_send.sh"

_ALL = [_TG_SEND, _NAZIM_SEND, _OEH_SEND]
_ALL_IDS = ["tg_send.sh", "nazim_send.sh", "oeh_send.sh"]


def _extract_block(path: Path, start: str, end: str) -> str:
    text = path.read_text()
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


# tg_send.sh / nazim_send.sh share the --ask/--chase-hours/--secret-vault-key
# loop; oeh_send.sh only has --secret-vault-key (no --ask machinery at all).
_ASK_SCRIPTS = [_TG_SEND, _NAZIM_SEND]
_ASK_START = 'ASK=""'
_OEH_START = 'SECRET_VAULT_KEY=""'
_END = 'TEXT="${1:-$(cat)}"'


def _run(block: str, argv: list) -> subprocess.CompletedProcess:
    harness = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        + block
        + '\necho "SVK=[$SECRET_VAULT_KEY]"\n'
          'echo "REST=[$*]"\n'
    )
    return subprocess.run(
        ["bash", "-c", harness, "--"] + argv,
        capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize("path", _ALL, ids=_ALL_IDS)
def test_scripts_exist_and_parse(path):
    assert path.is_file(), f"missing {path}"
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("path", _ASK_SCRIPTS, ids=["tg_send.sh", "nazim_send.sh"])
def test_no_flags_leaves_secret_vault_key_empty(path):
    block = _extract_block(path, _ASK_START, _END)
    r = _run(block, ["hello there"])
    assert r.returncode == 0, r.stderr
    assert "SVK=[]" in r.stdout
    assert "REST=[hello there]" in r.stdout


@pytest.mark.parametrize("path", _ASK_SCRIPTS, ids=["tg_send.sh", "nazim_send.sh"])
def test_secret_vault_key_flag_is_captured_and_stripped_from_positional(path):
    block = _extract_block(path, _ASK_START, _END)
    r = _run(block, ["--secret-vault-key", "oeh_preview_password", "the message: {{SECRET}}"])
    assert r.returncode == 0, r.stderr
    assert "SVK=[oeh_preview_password]" in r.stdout
    assert "REST=[the message: {{SECRET}}]" in r.stdout


@pytest.mark.parametrize("path", _ASK_SCRIPTS, ids=["tg_send.sh", "nazim_send.sh"])
def test_secret_vault_key_combines_with_ask_flag(path):
    block = _extract_block(path, _ASK_START, _END)
    r = _run(block, ["--ask", "confirm receipt", "--secret-vault-key", "oeh_preview_password", "pw: {{SECRET}}"])
    assert r.returncode == 0, r.stderr
    assert "SVK=[oeh_preview_password]" in r.stdout
    assert "REST=[pw: {{SECRET}}]" in r.stdout


def test_oeh_send_no_flags_leaves_secret_vault_key_empty():
    block = _extract_block(_OEH_SEND, _OEH_START, _END)
    r = _run(block, ["hello there"])
    assert r.returncode == 0, r.stderr
    assert "SVK=[]" in r.stdout
    assert "REST=[hello there]" in r.stdout


def test_oeh_send_secret_vault_key_flag_is_captured_and_stripped_from_positional():
    block = _extract_block(_OEH_SEND, _OEH_START, _END)
    r = _run(block, ["--secret-vault-key", "oeh_preview_password", "the message: {{SECRET}}"])
    assert r.returncode == 0, r.stderr
    assert "SVK=[oeh_preview_password]" in r.stdout
    assert "REST=[the message: {{SECRET}}]" in r.stdout


@pytest.mark.parametrize("path", _ALL, ids=_ALL_IDS)
def test_empty_positional_array_does_not_error_under_set_dash_u(path):
    start = _ASK_START if path in _ASK_SCRIPTS else _OEH_START
    block = _extract_block(path, start, _END)
    r = _run(block, ["--secret-vault-key", "oeh_preview_password"])
    assert r.returncode == 0, r.stderr
    assert "unbound variable" not in r.stderr
    assert "REST=[]" in r.stdout


# ── static wiring assertions: the RESOLVED value must feed the SEND, never
# the LOG (bus #44378's core ask) ────────────────────────────────────────────

@pytest.mark.parametrize("path", _ALL, ids=_ALL_IDS)
def test_send_uses_send_text_not_raw_text(path):
    code = path.read_text()
    assert 'TG_TEXT="$SEND_TEXT"' in code, (
        f"{path.name}: the Telegram send must use SEND_TEXT (the {{SECRET}}-substituted "
        "copy), not the raw placeholder-bearing TEXT"
    )
    assert 'TG_TEXT="$TEXT"' not in code, (
        f"{path.name}: a stray TG_TEXT=\"$TEXT\" would send the literal {{SECRET}} "
        "placeholder to the recipient instead of the resolved value"
    )


@pytest.mark.parametrize("path", _ALL, ids=_ALL_IDS)
def test_durable_log_uses_raw_text_never_send_text(path):
    code = path.read_text()
    assert 'outbound "$TEXT"' in code, (
        f"{path.name}: the durable log must record TEXT (placeholder intact), "
        "matching every other outbound log call in this script"
    )
    assert 'outbound "$SEND_TEXT"' not in code, (
        f"{path.name}: logging SEND_TEXT would persist the resolved secret value — "
        "exactly the bus #44378 incident this change exists to prevent"
    )


@pytest.mark.parametrize("path", _ALL, ids=_ALL_IDS)
def test_vault_placeholder_helper_invoked_with_pre_substitution_template(path):
    code = path.read_text()
    assert "vault_placeholder_send.py" in code
    assert 'VPH_TEMPLATE="$TEXT"' in code, (
        f"{path.name}: the helper must receive the placeholder-bearing TEXT as its "
        "template, not an already-substituted SEND_TEXT"
    )
    assert 'VPH_VAULT_KEY="$SECRET_VAULT_KEY"' in code


@pytest.mark.parametrize("path", _ALL, ids=_ALL_IDS)
def test_secret_vault_key_never_reaches_argv_of_the_helper(path):
    """The vault key name + template are passed to vault_placeholder_send.py
    entirely via env (VPH_TEMPLATE/VPH_VAULT_KEY) — never as a positional
    argv entry to the python3 invocation, which would show up in `ps`."""
    code = path.read_text()
    assert 'vault_placeholder_send.py" "$SECRET_VAULT_KEY"' not in code
    assert 'vault_placeholder_send.py" "$TEXT"' not in code
    assert 'vault_placeholder_send.py" "$SEND_TEXT"' not in code
