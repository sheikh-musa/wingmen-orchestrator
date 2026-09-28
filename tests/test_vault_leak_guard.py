"""Tests for nervous_system.vault_leak_guard (bus #44378, tightened per
orch-console's PR #197 review, bus #44388).

Pure-logic tests: monkeypatch vault.get directly (same style as
tests/test_vault.py's monkeypatch of _local_kek / _local_host_id) so these
never touch a live DB, Keychain, or KEK. Proves: (a) a value that matches an
allowlisted vault key gets redacted with the key named in the marker, only
for a message tagged with one of that key's scoped tags, (b) an out-of-scope
tag never triggers a vault.get() at all (bus #44388 fix 2 — bounding
vault_access_log noise), (c) a vault lookup failure for one key never blocks
the rest of the text and comes back as a distinct "skipped" entry rather than
looking like "checked, clean" (fix 1b), (d) clean text with no allowlisted
value round-trips byte-identical, (e) the allowlist is a real, bounded
mapping — not a stand-in for "every vault value"."""
from __future__ import annotations

from types import SimpleNamespace

from nervous_system import vault_leak_guard as vlg
from nervous_system.vault import SecretNotFoundError, VaultError


def _fake_get_returning(value: str):
    def _get(name, reason):
        return SimpleNamespace(value=value, leak_flagged=False, leak_reason=None)
    return _get


def test_allowlisted_key_value_is_redacted_and_named(monkeypatch):
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", {"oeh_preview_password": ("oeh",)})
    monkeypatch.setattr(vlg.vault, "get", _fake_get_returning("Sunfl0wer!42"))

    text = "here's the preview password: Sunfl0wer!42 — let us know if it works"
    redacted, matched, skipped = vlg.defensive_redact(text, tag="oeh")

    assert "Sunfl0wer!42" not in redacted
    assert "[REDACTED: oeh_preview_password]" in redacted
    assert matched == ["oeh_preview_password"]
    assert skipped == []


def test_out_of_scope_tag_never_calls_vault_get(monkeypatch):
    calls = []

    def _get(name, reason):
        calls.append(name)
        return SimpleNamespace(value="Sunfl0wer!42", leak_flagged=False, leak_reason=None)

    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", {"oeh_preview_password": ("oeh",)})
    monkeypatch.setattr(vlg.vault, "get", _get)

    text = "here's the preview password: Sunfl0wer!42"
    redacted, matched, skipped = vlg.defensive_redact(text, tag="nazim-console")

    assert redacted == text
    assert matched == []
    assert skipped == []
    assert calls == []


def test_no_tag_never_calls_vault_get(monkeypatch):
    calls = []

    def _get(name, reason):
        calls.append(name)
        return SimpleNamespace(value="Sunfl0wer!42", leak_flagged=False, leak_reason=None)

    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", {"oeh_preview_password": ("oeh",)})
    monkeypatch.setattr(vlg.vault, "get", _get)

    redacted, matched, skipped = vlg.defensive_redact("password: Sunfl0wer!42", tag=None)
    assert matched == []
    assert calls == []


def test_clean_text_with_no_secret_value_round_trips_byte_identical(monkeypatch):
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", {"oeh_preview_password": ("oeh",)})
    monkeypatch.setattr(vlg.vault, "get", _fake_get_returning("Sunfl0wer!42"))

    clean = "Deploy finished for oeh. No secrets in this message at all."
    redacted, matched, skipped = vlg.defensive_redact(clean, tag="oeh")
    assert redacted == clean
    assert matched == []
    assert skipped == []


def test_empty_text_passthrough(monkeypatch):
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", {"oeh_preview_password": ("oeh",)})
    redacted, matched, skipped = vlg.defensive_redact("", tag="oeh")
    assert redacted == ""
    assert matched == []
    assert skipped == []


def test_vault_lookup_failure_for_in_scope_key_is_reported_as_skipped_not_clean(monkeypatch):
    def _raise(name, reason):
        raise SecretNotFoundError(f"no secret named {name!r}")
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", {"some_unbootstrapped_key": ("oeh",)})
    monkeypatch.setattr(vlg.vault, "get", _raise)

    text = "totally normal message"
    redacted, matched, skipped = vlg.defensive_redact(text, tag="oeh")
    assert redacted == text
    assert matched == []
    assert skipped == [("some_unbootstrapped_key", "SecretNotFoundError")]


def test_one_key_failing_does_not_block_a_later_key_matching(monkeypatch):
    calls = {"n": 0}

    def _get(name, reason):
        calls["n"] += 1
        if name == "broken_key":
            raise VaultError("KEK missing")
        return SimpleNamespace(value="hunter2", leak_flagged=False, leak_reason=None)

    monkeypatch.setattr(
        vlg, "CLIENT_SHAREABLE_VAULT_KEYS",
        {"broken_key": ("oeh",), "oeh_preview_password": ("oeh",)},
    )
    monkeypatch.setattr(vlg.vault, "get", _get)

    redacted, matched, skipped = vlg.defensive_redact("password is hunter2 today", tag="oeh")
    assert "hunter2" not in redacted
    assert matched == ["oeh_preview_password"]
    assert skipped == [("broken_key", "VaultError")]
    assert calls["n"] == 2


def test_defensive_redact_never_raises_on_unexpected_vault_error(monkeypatch):
    def _raise(name, reason):
        raise VaultError("some vault plumbing error")
    monkeypatch.setattr(vlg, "CLIENT_SHAREABLE_VAULT_KEYS", {"oeh_preview_password": ("oeh",)})
    monkeypatch.setattr(vlg.vault, "get", _raise)

    # must not raise
    redacted, matched, skipped = vlg.defensive_redact("anything", tag="oeh")
    assert redacted == "anything"
    assert matched == []
    assert skipped == [("oeh_preview_password", "VaultError")]


def test_allowlist_is_small_and_bounded_not_every_vault_value():
    # This is a deliberate ALLOWLIST, not "every vault secret" (see module
    # docstring: decrypting every stored secret on every log call is
    # unbounded cost). Guard against it silently growing into that shape, and
    # against a key losing its tag scope (bus #44388 fix 2 — an unscoped key
    # is exactly the fleet-wide-scan shape orch-console rejected).
    assert 1 <= len(vlg.CLIENT_SHAREABLE_VAULT_KEYS) <= 20
    assert "oeh_preview_password" in vlg.CLIENT_SHAREABLE_VAULT_KEYS
    for key, tags in vlg.CLIENT_SHAREABLE_VAULT_KEYS.items():
        assert isinstance(tags, tuple) and len(tags) >= 1, (
            f"{key!r} has no scoping tags — an empty/missing scope would make "
            "defensive_redact check it on every message tag, reintroducing the "
            "fleet-wide vault_access_log noise bus #44388 fix 2 closed"
        )
    assert "oeh" in vlg.CLIENT_SHAREABLE_VAULT_KEYS["oeh_preview_password"]


def test_none_text_passthrough_without_raising():
    # log()'s text is always a str in practice, but None must never crash the
    # last line of defense before a durable-log insert.
    redacted, matched, skipped = vlg.defensive_redact(None, tag="oeh")
    assert redacted is None
    assert matched == []
    assert skipped == []
