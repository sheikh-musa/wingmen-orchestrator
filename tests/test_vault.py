"""Tests for nervous_system.vault (op#21338 phase 1).

Two layers, since this host's Keychain entry hasn't been bootstrapped yet
(orch-console owns that step, bus #41841 note 1):

1. Pure crypto round-trip against the internal AEAD helpers -- no DB, no
   Keychain, proves the envelope-encryption primitives themselves are correct.
2. A real vault.put() call against the live DB with a throwaway secret name,
   which is EXPECTED to fail with KekNotFoundError right now (no Keychain
   entry yet) -- asserts the failure is the correct, actionable one (names
   the exact service/account to bootstrap), not a wrong error swallowed or
   worked around. Cleans up any row it might still have written before failing
   (put() fails before any row is inserted in this path, but the cleanup is
   unconditional so the test is safe to re-run either way).
"""
import os

import psycopg
import pytest
from dotenv import load_dotenv

from nervous_system.vault import (
    KekNotFoundError,
    _aead_decrypt,
    _aead_encrypt,
    _local_host_id,
    vault,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, ".env"))

# vault._agent_id() fails loud without CC_BASE_AGENT_ID/AGENT_ID (by design --
# see reference_agent_id_not_orch_agent_id_for_identity). A bare shell running
# this file directly (no lane env) hits that fail-loud and gets a confusing
# collateral failure unrelated to what's actually being tested. Default to a
# clearly-test-only identity so it doesn't masquerade as a real agent in the
# audit log, but don't clobber a real ambient identity if one is already set.
os.environ.setdefault("CC_BASE_AGENT_ID", "vault-test-suite")
os.environ.setdefault("AGENT_ID", "vault-test-suite")

# CI-hardening (backlog#68): the crypto tests below need no DB and always run.
# The DB-backed tests need DATABASE_URL (skip cleanly when no DSN, e.g. CI with
# no secret — they still RUN wherever a DSN is set), and the host-identity test
# is Mini-only (skip on any other host, e.g. the ubuntu CI runner).
_needs_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="requires DATABASE_URL (DB-integration test)",
)
_needs_mini = pytest.mark.skipif(
    _local_host_id() != "mini",
    reason="host-coupled: asserts the Mini (Darwin) identity",
)

TEST_SECRET_NAME = "vault_selftest_op21338"


def test_aead_round_trip():
    key = os.urandom(32)
    blob = _aead_encrypt(key, b"correct horse battery staple")
    assert _aead_decrypt(key, blob) == b"correct horse battery staple"


def test_aead_wrong_key_fails():
    key = os.urandom(32)
    wrong_key = os.urandom(32)
    blob = _aead_encrypt(key, b"secret")
    with pytest.raises(Exception):
        _aead_decrypt(wrong_key, blob)


@_needs_mini
def test_local_host_id_is_mini_on_this_host():
    # This test suite runs on the Mac Mini (Darwin).
    assert _local_host_id() == "mini"


@pytest.fixture(autouse=True)
def _cleanup_test_secret():
    yield
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.vault_secrets WHERE name = %s", (TEST_SECRET_NAME,))
            cur.execute("DELETE FROM public.vault_access_log WHERE secret_name = %s", (TEST_SECRET_NAME,))
        conn.commit()
    finally:
        conn.close()


@_needs_db
def test_put_fails_with_actionable_kek_error_for_unbootstrapped_host(monkeypatch):
    """Original intent (op#21338 phase 1): before orch-console bootstrapped the
    Mini's real Keychain entry (bus #41846), this test exercised that exact
    missing-entry path against ambient machine state. The entry now genuinely
    exists here, so that natural precondition is gone -- decouple from ambient
    state instead by pointing _local_host_id() at an account name that
    deliberately has no Keychain entry. This still exercises the REAL macOS
    `security find-generic-password` call (unmocked) and must fail with
    KekNotFoundError naming the exact entry to create -- not a generic error,
    not a silent no-op."""
    fake_host = "vault-test-nonexistent-host-op21338"
    monkeypatch.setattr("nervous_system.vault._local_host_id", lambda: fake_host)
    with pytest.raises(KekNotFoundError) as exc_info:
        vault.put(TEST_SECRET_NAME, "throwaway-test-value", reason="vault phase 1 self-test")
    msg = str(exc_info.value)
    assert "wingmen-vault-kek" in msg
    assert fake_host in msg
    assert "security add-generic-password" in msg


@_needs_db
def test_get_fails_for_nonexistent_secret():
    from nervous_system.vault import SecretNotFoundError

    with pytest.raises(SecretNotFoundError):
        vault.get("vault_selftest_definitely_does_not_exist_op21338", reason="self-test")


@_needs_db
def test_full_put_get_round_trip_with_fake_kek(monkeypatch):
    """Proves the WHOLE pipeline (DB write/read, wrap/unwrap, AEAD, audit log)
    end to end, independent of whether the real Mini Keychain entry has been
    bootstrapped yet -- monkeypatches _local_kek to a fixed throwaway key so
    this test never touches real Keychain state and is safe to re-run.
    Deliberately separate from the KEK-not-found test above: that one proves
    the failure path is correct; this one proves the success path is correct.
    """
    import nervous_system.vault as vault_mod

    fake_key = os.urandom(32)
    monkeypatch.setattr(vault_mod, "_local_kek", lambda host_id: fake_key)

    vault.put(TEST_SECRET_NAME, "s3cret-round-trip-value", reason="vault phase 1 self-test (fake KEK)")
    secret = vault.get(TEST_SECRET_NAME, reason="vault phase 1 self-test (fake KEK)")
    assert secret.value == "s3cret-round-trip-value"
    assert secret.leak_flagged is False
    assert secret.leak_reason is None

    dsn = os.environ.get("DATABASE_URL")
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.vault_access_log WHERE secret_name = %s AND success = true",
                (TEST_SECRET_NAME,),
            )
            (audit_count,) = cur.fetchone()
    finally:
        conn.close()
    assert audit_count >= 2  # one for put, one for get


@_needs_db
def test_leak_flagged_surfaces_and_rotate_clears_it(monkeypatch):
    import nervous_system.vault as vault_mod

    fake_key = os.urandom(32)
    monkeypatch.setattr(vault_mod, "_local_kek", lambda host_id: fake_key)

    vault.put(
        TEST_SECRET_NAME,
        "old-value",
        reason="self-test",
        leak_flagged=True,
        leak_flagged_reason="self-test leak simulation",
    )
    secret = vault.get(TEST_SECRET_NAME, reason="self-test")
    assert secret.leak_flagged is True
    assert secret.leak_reason == "self-test leak simulation"

    vault.rotate(TEST_SECRET_NAME, "new-value", reason="self-test rotation")
    secret = vault.get(TEST_SECRET_NAME, reason="self-test")
    assert secret.value == "new-value"
    assert secret.leak_flagged is False
    assert secret.leak_reason is None
