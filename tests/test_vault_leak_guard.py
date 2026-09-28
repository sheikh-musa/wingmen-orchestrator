"""Tests for nervous_system.vault_leak_guard (bus #44378).

Pure-logic tests: monkeypatch vault.get directly (same style as
tests/test_vault.py's monkeypatch of _local_kek / _local_host_id) so these
never touch a live DB, Keychain, or KEK. Proves: (a) a value that matches an
allowlisted vault key gets redacted with the key named in the marker, (b) a
vault lookup failure for one key never blocks the rest of the text, (c) clean
text with no allowlisted value round-trips byte-identical, (d) the allowlist
is a real, bounded allowlist — not a stand-in for "every vault value"."""
from __future__ import annotations

from types import SimpleNamespace

from nervous_system import vault_leak_guard as vlg
from nervous_system.vault import SecretNotFoundError, VaultError


def _fake_get_returning(value: str):
    def _get(name, reason):
        return SimpleNamespace(value=value, leak_flagged=False, leak_reason=None)
    return _get


def test_allowlisted_key_value_is_redacted_and_named(monkeypatch):
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", ("oeh_preview_password",))
    monkeypatch.setattr(vlg.vault, "get", _fake_get_returning("Sunfl0wer!42"))

    text = "here's the preview password: Sunfl0wer!42 — let us know if it works"
    redacted, matched = vlg.defensive_redact(text)

    assert "Sunfl0wer!42" not in redacted
    assert "[REDACTED: oeh_preview_password]" in redacted
    assert matched == ["oeh_preview_password"]


def test_clean_text_with_no_secret_value_round_trips_byte_identical(monkeypatch):
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", ("oeh_preview_password",))
    monkeypatch.setattr(vlg.vault, "get", _fake_get_returning("Sunfl0wer!42"))

    clean = "Deploy finished for oeh. No secrets in this message at all."
    redacted, matched = vlg.defensive_redact(clean)
    assert redacted == clean
    assert matched == []


def test_empty_text_passthrough(monkeypatch):
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", ("oeh_preview_password",))
    redacted, matched = vlg.defensive_redact("")
    assert redacted == ""
    assert matched == []


def test_vault_lookup_failure_for_one_key_never_raises_and_leaves_text_unredacted(monkeypatch):
    def _raise(name, reason):
        raise SecretNotFoundError(f"no secret named {name!r}")
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", ("some_unbootstrapped_key",))
    monkeypatch.setattr(vlg.vault, "get", _raise)

    text = "totally normal message"
    redacted, matched = vlg.defensive_redact(text)
    assert redacted == text
    assert matched == []


def test_one_key_failing_does_not_block_a_later_key_matching(monkeypatch):
    calls = {"n": 0}

    def _get(name, reason):
        calls["n"] += 1
        if name == "broken_key":
            raise VaultError("KEK missing")
        return SimpleNamespace(value="hunter2", leak_flagged=False, leak_reason=None)

    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", ("broken_key", "oeh_preview_password"))
    monkeypatch.setattr(vlg.vault, "get", _get)

    redacted, matched = vlg.defensive_redact("password is hunter2 today")
    assert "hunter2" not in redacted
    assert matched == ["oeh_preview_password"]
    assert calls["n"] == 2


def test_defensive_redact_never_raises_on_unexpected_vault_error(monkeypatch):
    def _raise(name, reason):
        raise VaultError("some vault plumbing error")
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", ("oeh_preview_password",))
    monkeypatch.setattr(vlg.vault, "get", _raise)

    # must not raise
    redacted, matched = vlg.defensive_redact("anything")
    assert redacted == "anything"
    assert matched == []


def test_allowlist_is_small_and_bounded_not_every_vault_value():
    # This is a deliberate ALLOWLIST, not "every vault secret" (see module
    # docstring: decrypting every stored secret on every log call is
    # unbounded cost). Guard against it silently growing into that shape.
    assert 1 <= len(vlg.CLIENT_SHAREABLE_VAULT_KEYS) <= 20
    assert "oeh_preview_password" in vlg.CLIENT_SHAREABLE_VAULT_KEYS


def test_none_text_passthrough_without_raising():
    # log()'s text is always a str in practice, but None must never crash the
    # last line of defense before a durable-log insert.
    redacted, matched = vlg.defensive_redact(None)
    assert redacted is None
    assert matched == []
