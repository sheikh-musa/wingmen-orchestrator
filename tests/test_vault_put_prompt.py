"""vault_put_prompt.py — the no-echo, no-argv secret intake helper (Musa
op#35814, bus #82358): Musa's sanctioned way to hand the fleet a brand-new
secret without ever pasting it into Telegram/a bus message/argv/shell
history. Exercises the real main() in-process (not a subprocess) so getpass
and the vault call can be mocked without a real TTY or a DB connection —
same reasoning test_vault.py gives for why ITS DB-backed tests skip without
a DSN: this file never needs the DB at all, since every path it covers
either exits before vault.put() or calls a mocked vault.put().
"""
import importlib.util
import os
import sys
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "lib", "vault_put_prompt.py")


def _load_module():
    """Import the real script FILE (not via package path) as a fresh module
    each call, so per-test monkeypatches of its module-level names never
    leak between tests."""
    spec = importlib.util.spec_from_file_location("vault_put_prompt_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    m = _load_module()
    yield m
    sys.modules.pop("vault_put_prompt_under_test", None)


def test_refuses_wrong_arg_count(mod, capsys):
    rc = mod.main(["vault_put_prompt.py", "ONLY_NAME"])
    assert rc == 1
    assert "usage" in capsys.readouterr().err


def test_refuses_non_interactive_stdin(mod, capsys):
    with mock.patch.object(sys.stdin, "isatty", return_value=False):
        rc = mod.main(["vault_put_prompt.py", "DEEPSEEK_KEY_BARIQ", "reason"])
    assert rc == 1
    assert "non-interactively" in capsys.readouterr().err


def test_empty_value_aborts_without_calling_vault_put(mod, capsys):
    with mock.patch.object(sys.stdin, "isatty", return_value=True), \
         mock.patch.object(mod.getpass, "getpass", return_value=""), \
         mock.patch.object(mod, "vault") as m_vault:
        rc = mod.main(["vault_put_prompt.py", "DEEPSEEK_KEY_BARIQ", "reason"])
    assert rc == 1
    assert "empty value" in capsys.readouterr().err
    m_vault.put.assert_not_called()


def test_mismatched_confirmation_aborts_without_calling_vault_put(mod, capsys):
    with mock.patch.object(sys.stdin, "isatty", return_value=True), \
         mock.patch.object(mod.getpass, "getpass", side_effect=["sk-real-value", "sk-TYPO"]), \
         mock.patch.object(mod, "vault") as m_vault:
        rc = mod.main(["vault_put_prompt.py", "DEEPSEEK_KEY_BARIQ", "reason"])
    assert rc == 1
    assert "did not match" in capsys.readouterr().err
    m_vault.put.assert_not_called()


def test_matching_confirmation_calls_vault_put_with_exact_value_and_prints_only_metadata(mod, capsys):
    with mock.patch.object(sys.stdin, "isatty", return_value=True), \
         mock.patch.object(mod.getpass, "getpass", side_effect=["sk-real-secret-value", "sk-real-secret-value"]), \
         mock.patch.object(mod, "vault") as m_vault, \
         mock.patch.object(mod, "_connect") as m_connect:
        fake_conn = mock.MagicMock()
        fake_cursor = mock.MagicMock()
        fake_cursor.fetchone.return_value = ("DEEPSEEK_KEY_BARIQ", "2026-10-29 15:00:00+00:00")
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
        m_connect.return_value = fake_conn

        rc = mod.main(["vault_put_prompt.py", "DEEPSEEK_KEY_BARIQ", "op#35814 per-client key"])

    assert rc == 0
    m_vault.put.assert_called_once_with("DEEPSEEK_KEY_BARIQ", "sk-real-secret-value", reason="op#35814 per-client key")
    out = capsys.readouterr().out
    assert "sk-real-secret-value" not in out, "the secret value must NEVER be printed"
    assert "DEEPSEEK_KEY_BARIQ" in out
    assert "2026-10-29" in out
    # The metadata query itself must be a SELECT on name/created_at only —
    # never select the ciphertext column.
    query = fake_cursor.execute.call_args[0][0]
    assert "ciphertext" not in query.lower()
    assert "select" in query.lower() and "created_at" in query.lower()


def test_vault_put_failure_reports_and_does_not_crash(mod, capsys):
    with mock.patch.object(sys.stdin, "isatty", return_value=True), \
         mock.patch.object(mod.getpass, "getpass", side_effect=["sk-x", "sk-x"]), \
         mock.patch.object(mod, "vault") as m_vault:
        m_vault.put.side_effect = mod.VaultError("kek not found")
        rc = mod.main(["vault_put_prompt.py", "DEEPSEEK_KEY_BARIQ", "reason"])
    assert rc == 1
    assert "vault.put failed" in capsys.readouterr().err


def test_value_never_passed_as_a_cli_argument_in_the_shipped_wrapper():
    """Structural guard on the .sh entrypoint: the secret must never be
    accepted as $3 (or any positional arg) — only NAME and reason."""
    wrapper = os.path.join(_ROOT, "scripts", "vault_put_prompt.sh")
    with open(wrapper) as f:
        body = f.read()
    assert '"$1"' in body and '"$2"' in body
    assert '"$3"' not in body
