"""scripts/put_client_secret.py — the ONE sanctioned road from a client store to
the vault without printing the value (orch-console bus #87265, P0, Musa op 36795).

What is asserted, and why each one matters:

  * the value NEVER appears on stdout/stderr, in either dry-run or write mode —
    that is the whole point of the helper;
  * the only success output is the vault name, a sha256[:8] prefix and the length
    (the prefix is what a later hash-matched transcript sweep searches for);
  * the value is never accepted as an argv argument (argv lands in shell history
    and in the transcript of the very command that was meant to protect it);
  * the vault write is audited with the reason, and a rejected write is reported
    by error class only — never with the library's own message, which can carry a
    connection string.
"""
import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "put_client_secret.py"

VALUE = "synthetic-client-credential-not-a-real-secret"


def _load_module():
    spec = importlib.util.spec_from_file_location("put_client_secret_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pcs():
    return _load_module()


@pytest.fixture
def fake_vault(monkeypatch):
    """Stub nervous_system.vault so the write path is exercised with no DB."""
    calls = []

    class VaultError(RuntimeError):
        pass

    class _Vault:
        def put(self, name, value, reason, leak_flagged=False, leak_flagged_reason=None):
            calls.append({
                "name": name, "value": value, "reason": reason,
                "leak_flagged": leak_flagged, "leak_flagged_reason": leak_flagged_reason,
            })

    mod = types.ModuleType("nervous_system.vault")
    mod.vault = _Vault()
    mod.VaultError = VaultError
    import nervous_system  # the real package, so only the submodule is stubbed
    monkeypatch.setitem(sys.modules, "nervous_system.vault", mod)
    # ALSO the package attribute: on Python 3.9 `import nervous_system.vault as x`
    # resolves through nervous_system.vault (the PACKAGE ATTRIBUTE) when the real
    # submodule has ever been imported, so setitem alone leaves two different
    # modules reachable under one name. That is not theoretical — it made this
    # test mutate the REAL vault singleton's put() (patching sys.modules while the
    # `as` form handed back the real module) and poisoned four tests/test_vault.py
    # cases in CI run 41227818506.
    monkeypatch.setattr(nervous_system, "vault", mod, raising=False)
    return calls, VaultError


@pytest.fixture
def piped(monkeypatch):
    def _set(text):
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    return _set


def test_dry_run_prints_only_name_hash_prefix_and_length(pcs, piped, capsys):
    piped(VALUE + "\n")
    rc = pcs.main(["MODESTY_HUT_WC_FB_FEED_SECRET", "--reason", "test", "--dry-run"])
    out = capsys.readouterr()

    assert rc == 0
    assert VALUE not in out.out and VALUE not in out.err
    assert out.out.startswith("DRY-RUN MODESTY_HUT_WC_FB_FEED_SECRET sha256:")
    assert f"len={len(VALUE)}" in out.out


def test_write_path_stores_the_value_and_prints_no_value(pcs, fake_vault, piped, capsys):
    calls, _ = fake_vault
    piped(VALUE + "\n")

    rc = pcs.main(["MODESTY_HUT_WC_FB_FEED_SECRET", "--reason", "vaulting the woo feed secret"])
    out = capsys.readouterr()

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["value"] == VALUE
    assert calls[0]["reason"] == "vaulting the woo feed secret"
    assert VALUE not in out.out and VALUE not in out.err
    assert out.out.startswith("OK MODESTY_HUT_WC_FB_FEED_SECRET sha256:")
    assert f"len={len(VALUE)}" in out.out


def test_hash_prefix_matches_what_a_transcript_sweep_would_search_for(pcs, piped, capsys):
    import hashlib
    piped(VALUE)
    pcs.main(["NAME_A", "--reason", "r", "--dry-run"])
    out = capsys.readouterr().out
    assert hashlib.sha256(VALUE.encode()).hexdigest()[:8] in out


def test_value_from_env_is_supported(pcs, fake_vault, monkeypatch, capsys):
    calls, _ = fake_vault
    monkeypatch.setenv("SOME_CLIENT_SECRET", VALUE)
    rc = pcs.main(["NAME_B", "--reason", "r", "--from-env", "SOME_CLIENT_SECRET"])
    out = capsys.readouterr()
    assert rc == 0
    assert calls[0]["value"] == VALUE
    assert VALUE not in out.out and VALUE not in out.err


def test_missing_env_var_fails_without_writing(pcs, fake_vault, capsys):
    calls, _ = fake_vault
    rc = pcs.main(["NAME_C", "--reason", "r", "--from-env", "DEFINITELY_NOT_SET_XYZ"])
    assert rc == 1
    assert calls == []
    assert "not set" in capsys.readouterr().err


def test_value_from_file_is_supported(pcs, fake_vault, tmp_path, capsys):
    calls, _ = fake_vault
    p = tmp_path / "cred.txt"
    p.write_text(VALUE + "\n")
    rc = pcs.main(["NAME_D", "--reason", "r", "--from-file", str(p)])
    out = capsys.readouterr()
    assert rc == 0
    assert calls[0]["value"] == VALUE  # the trailing newline is stripped
    assert VALUE not in out.out


def test_empty_value_writes_nothing(pcs, fake_vault, piped, capsys):
    calls, _ = fake_vault
    piped("   \n")
    rc = pcs.main(["NAME_E", "--reason", "r"])
    assert rc == 1
    assert calls == []
    assert "empty" in capsys.readouterr().err


def test_empty_reason_is_refused(pcs, fake_vault, piped, capsys):
    calls, _ = fake_vault
    piped(VALUE)
    rc = pcs.main(["NAME_F", "--reason", "  "])
    assert rc == 2
    assert calls == []
    assert "audit" in capsys.readouterr().err


def test_invalid_vault_name_is_refused(pcs, piped, capsys):
    piped(VALUE)
    rc = pcs.main(["bad name!", "--reason", "r"])
    assert rc == 2
    assert "invalid vault name" in capsys.readouterr().err


def test_vault_failure_is_reported_by_class_only(pcs, fake_vault, piped, capsys):
    fake_vault

    class Boom(RuntimeError):
        pass

    # Take the STUB from sys.modules explicitly. `import nervous_system.vault as sv`
    # hands back the package attribute, which may be the REAL module — mutating that
    # would leak into every later test in the same process.
    sv = sys.modules["nervous_system.vault"]
    assert sv.vault.__class__.__name__ == "_Vault", "not the fixture's stub"

    def _raise(*a, **k):
        raise Boom("SENSITIVE-ERROR-DETAIL that must not be relayed")

    sv.vault.put = _raise
    piped(VALUE)
    rc = pcs.main(["NAME_G", "--reason", "r"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Boom" in err
    assert VALUE not in err
    # the library message itself must not be relayed — it can carry a connection string
    assert "SENSITIVE-ERROR-DETAIL" not in err


def test_value_is_never_accepted_as_an_argument(pcs):
    """`name` is the only positional — a second one (the value) is an argparse error,
    so a value can never reach argv/shell history/transcript through this helper."""
    with pytest.raises(SystemExit):
        pcs.main(["NAME_H", VALUE, "--reason", "r"])
