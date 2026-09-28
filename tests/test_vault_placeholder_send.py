"""Tests for scripts/lib/vault_placeholder_send.py (bus #44378).

The --secret-vault-key mechanism: a send script gives this helper a template
containing a literal {{SECRET}} placeholder plus a vault key name; the helper
fetches the value in-process and returns ONLY the substituted text. These
tests monkeypatch vault.get (same style as tests/test_vault.py) so nothing
here touches a live DB, Keychain, or KEK.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nervous_system.vault import SecretNotFoundError, VaultError

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "vault_placeholder_send.py"
_spec = importlib.util.spec_from_file_location("vault_placeholder_send", _MODULE_PATH)
vps = importlib.util.module_from_spec(_spec)
sys.modules["vault_placeholder_send"] = vps
_spec.loader.exec_module(vps)


def _fake_get_returning(value: str):
    def _get(name, reason):
        return SimpleNamespace(value=value, leak_flagged=False, leak_reason=None)
    return _get


# ── substitute(): the pure core ───────────────────────────────────────────────

def test_substitute_replaces_placeholder_with_vault_value():
    out = vps.substitute(
        "here is the password: {{SECRET}} — enjoy",
        "oeh_preview_password",
        get=_fake_get_returning("Sunfl0wer!42"),
    )
    assert out == "here is the password: Sunfl0wer!42 — enjoy"


def test_substitute_raises_value_error_when_no_placeholder():
    with pytest.raises(ValueError, match="no .*placeholder"):
        vps.substitute(
            "just a normal message, no placeholder here",
            "oeh_preview_password",
            get=_fake_get_returning("Sunfl0wer!42"),
        )


def test_substitute_propagates_vault_error_for_missing_secret():
    def _raise(name, reason):
        raise SecretNotFoundError(f"no secret named {name!r}")
    with pytest.raises(VaultError):
        vps.substitute("password: {{SECRET}}", "nonexistent_key", get=_raise)


def test_substitute_only_replaces_the_placeholder_never_touches_rest_of_text():
    out = vps.substitute(
        "{{SECRET}} is your {{SECRET}} for today",
        "k",
        get=_fake_get_returning("XYZ"),
    )
    # str.replace replaces every occurrence — both placeholders resolve to the
    # same fetched value, never a partial/garbled substitution.
    assert out == "XYZ is your XYZ for today"


# ── main(): env-in / stdout-out CLI contract ──────────────────────────────────

def test_main_writes_substituted_text_to_stdout_and_returns_0(monkeypatch, capsys):
    monkeypatch.setattr(vps.vault, "get", _fake_get_returning("Sunfl0wer!42"))
    monkeypatch.setenv("VPH_TEMPLATE", "pw: {{SECRET}}")
    monkeypatch.setenv("VPH_VAULT_KEY", "oeh_preview_password")

    rc = vps.main()
    assert rc == 0
    assert capsys.readouterr().out == "pw: Sunfl0wer!42"


def test_main_returns_1_and_never_prints_a_value_when_placeholder_missing(monkeypatch, capsys):
    monkeypatch.setattr(vps.vault, "get", _fake_get_returning("Sunfl0wer!42"))
    monkeypatch.setenv("VPH_TEMPLATE", "no placeholder in this message")
    monkeypatch.setenv("VPH_VAULT_KEY", "oeh_preview_password")

    rc = vps.main()
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert "Sunfl0wer!42" not in out.err


def test_main_returns_2_when_vault_key_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("VPH_VAULT_KEY", raising=False)
    monkeypatch.setenv("VPH_TEMPLATE", "pw: {{SECRET}}")
    rc = vps.main()
    assert rc == 2
    assert capsys.readouterr().out == ""


def test_main_returns_2_and_never_prints_a_value_on_vault_lookup_failure(monkeypatch, capsys):
    def _raise(name, reason):
        raise SecretNotFoundError(f"no secret named {name!r}")
    monkeypatch.setattr(vps.vault, "get", _raise)
    monkeypatch.setenv("VPH_TEMPLATE", "pw: {{SECRET}}")
    monkeypatch.setenv("VPH_VAULT_KEY", "does_not_exist")

    rc = vps.main()
    out = capsys.readouterr()
    assert rc == 2
    assert out.out == ""
