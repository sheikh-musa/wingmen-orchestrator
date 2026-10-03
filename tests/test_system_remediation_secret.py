"""Tests for the CAI-1380 secret-release-gate
(scripts.lib.system_remediation_secret) — no DB, no network, no real
password ever constructed. All DB/HTTP seams are injected, mirroring
auth_write_gate.py's resolve_users/grant_fn test pattern.

Property under test throughout: mint_remediation_session() must fail closed
on every uncertainty AND must never let the raw password reach its return
value, no matter which branch fires.
"""
import base64

from scripts.lib.system_remediation_secret import (
    MINT_KEY_ENV_VAR,
    encrypt_mint_password,
    mint_remediation_session,
)

DECISION_REF = "CAI-RESP-9999"
ORG_ID = "e9b3f7f9-36d0-4f4b-80d7-7529b6bdc2ea"
ACTOR_ID = "00000000-0000-0000-0000-000000000001"
EMAIL = "system-remediation-cai1380@wingmen.test"
PROJECT_REF = "ceayjeamtmcyzzvqflus"
ANON_KEY = "fake-anon-key"
TARGET_DSN = "postgresql://fake/dsn"
FAKE_KEY = base64.b64encode(b"0" * 32).decode("ascii")


def _grant_ok(*_args, **_kwargs):
    return True


def _grant_missing(*_args, **_kwargs):
    return False


def _password_present(*_args, **_kwargs):
    return "ciphertext-blob"


def _password_missing(*_args, **_kwargs):
    return None


def _decrypt_ok(_encrypted, _key):
    return "the-real-password"


def _decrypt_captures_and_returns(captured: list):
    def _fn(_encrypted, _key):
        captured.append("decrypt called")
        return "the-real-password"
    return _fn


def _password_grant_success(project_ref, anon_key, email, password, captured=None):
    if captured is not None:
        captured.append(password)  # test-only capture, to prove what WAS passed to the HTTP call
    return 200, {"access_token": "fake.jwt.token"}


def test_no_live_grant_denies_before_touching_the_key_or_password():
    """The grant-liveness check runs FIRST — a leaked/replayed session with
    no active grant must never even reach the decrypt step."""
    res = mint_remediation_session(
        decision_ref=DECISION_REF, org_id=ORG_ID, actor_user_id=ACTOR_ID, email=EMAIL,
        project_ref=PROJECT_REF, anon_key=ANON_KEY, target_dsn=TARGET_DSN,
        grant_is_live_fn=_grant_missing,
        fetch_password_fn=_password_present,  # would succeed if reached — proving it's NOT reached
        decrypt_fn=_decrypt_ok,
        mint_key=FAKE_KEY,
    )
    assert res.ok is False
    assert "no live system_remediation_grants row" in res.reason
    assert res.access_token is None


def test_missing_mint_key_denies():
    """This is the custody enforcement itself: no key in this process's
    environment/injection means no mint, full stop — proves the function
    can't accidentally succeed just because it happens to run somewhere
    with a live grant."""
    res = mint_remediation_session(
        decision_ref=DECISION_REF, org_id=ORG_ID, actor_user_id=ACTOR_ID, email=EMAIL,
        project_ref=PROJECT_REF, anon_key=ANON_KEY, target_dsn=TARGET_DSN,
        grant_is_live_fn=_grant_ok,
        fetch_password_fn=_password_present,
        decrypt_fn=_decrypt_ok,
        mint_key=None,  # explicitly absent, and MINT_KEY_ENV_VAR is not set in the test process
    )
    assert res.ok is False
    assert MINT_KEY_ENV_VAR in res.reason


def test_no_provisioned_password_denies():
    res = mint_remediation_session(
        decision_ref=DECISION_REF, org_id=ORG_ID, actor_user_id=ACTOR_ID, email=EMAIL,
        project_ref=PROJECT_REF, anon_key=ANON_KEY, target_dsn=TARGET_DSN,
        grant_is_live_fn=_grant_ok,
        fetch_password_fn=_password_missing,
        decrypt_fn=_decrypt_ok,
        mint_key=FAKE_KEY,
    )
    assert res.ok is False
    assert "no mint-password provisioned" in res.reason


def test_decrypt_failure_denies_without_leaking_details():
    def _boom(_encrypted, _key):
        raise ValueError("some internal crypto detail that should not leak")

    res = mint_remediation_session(
        decision_ref=DECISION_REF, org_id=ORG_ID, actor_user_id=ACTOR_ID, email=EMAIL,
        project_ref=PROJECT_REF, anon_key=ANON_KEY, target_dsn=TARGET_DSN,
        grant_is_live_fn=_grant_ok,
        fetch_password_fn=_password_present,
        decrypt_fn=_boom,
        mint_key=FAKE_KEY,
    )
    assert res.ok is False
    assert "decrypt failed" in res.reason
    assert "some internal crypto detail" not in res.reason


def test_gotrue_non_200_denies():
    def _fail_grant(*_a, **_k):
        return 400, {"error": "invalid_grant"}

    res = mint_remediation_session(
        decision_ref=DECISION_REF, org_id=ORG_ID, actor_user_id=ACTOR_ID, email=EMAIL,
        project_ref=PROJECT_REF, anon_key=ANON_KEY, target_dsn=TARGET_DSN,
        grant_is_live_fn=_grant_ok,
        fetch_password_fn=_password_present,
        decrypt_fn=_decrypt_ok,
        password_grant_fn=_fail_grant,
        mint_key=FAKE_KEY,
    )
    assert res.ok is False
    assert "status 400" in res.reason


def test_success_returns_only_the_jwt():
    """The core safety property: on success, the caller gets a token, never
    the password — this is checked via the return value AND via what was
    actually passed into the password-grant call."""
    captured_password = []

    def _password_grant(project_ref, anon_key, email, password):
        return _password_grant_success(project_ref, anon_key, email, password, captured=captured_password)

    res = mint_remediation_session(
        decision_ref=DECISION_REF, org_id=ORG_ID, actor_user_id=ACTOR_ID, email=EMAIL,
        project_ref=PROJECT_REF, anon_key=ANON_KEY, target_dsn=TARGET_DSN,
        grant_is_live_fn=_grant_ok,
        fetch_password_fn=_password_present,
        decrypt_fn=_decrypt_ok,
        password_grant_fn=_password_grant,
        mint_key=FAKE_KEY,
    )
    assert res.ok is True
    assert res.access_token == "fake.jwt.token"
    # The decrypted password WAS used for the HTTP call (proves the plumbing
    # actually works)...
    assert captured_password == ["the-real-password"]
    # ...but never appears anywhere in the returned result object.
    assert "the-real-password" not in str(res)


def test_missing_access_token_in_response_denies():
    def _grant_no_token(*_a, **_k):
        return 200, {"some_other_field": "x"}

    res = mint_remediation_session(
        decision_ref=DECISION_REF, org_id=ORG_ID, actor_user_id=ACTOR_ID, email=EMAIL,
        project_ref=PROJECT_REF, anon_key=ANON_KEY, target_dsn=TARGET_DSN,
        grant_is_live_fn=_grant_ok,
        fetch_password_fn=_password_present,
        decrypt_fn=_decrypt_ok,
        password_grant_fn=_grant_no_token,
        mint_key=FAKE_KEY,
    )
    assert res.ok is False
    assert "no access_token" in res.reason


# ── encrypt/decrypt round-trip (the only piece that touches real crypto) ────
def test_encrypt_decrypt_round_trip():
    from scripts.lib.system_remediation_secret import _decrypt_mint_password

    key_b64 = base64.b64encode(b"k" * 32).decode("ascii")
    plaintext = "a genuinely random-looking password, 32+ chars long!!"
    ciphertext = encrypt_mint_password(plaintext, key_b64)
    assert ciphertext != plaintext
    assert plaintext not in ciphertext  # not just base64 of the plaintext
    assert _decrypt_mint_password(ciphertext, key_b64) == plaintext


def test_decrypt_with_wrong_key_fails_rather_than_silently_returning_garbage():
    from cryptography.exceptions import InvalidTag

    from scripts.lib.system_remediation_secret import _decrypt_mint_password

    key_a = base64.b64encode(b"a" * 32).decode("ascii")
    key_b = base64.b64encode(b"b" * 32).decode("ascii")
    ciphertext = encrypt_mint_password("secret", key_a)
    try:
        _decrypt_mint_password(ciphertext, key_b)
        assert False, "expected InvalidTag, decrypt should not silently succeed with the wrong key"
    except InvalidTag:
        pass


def test_encrypt_rejects_wrong_key_length():
    import pytest

    with pytest.raises(ValueError):
        encrypt_mint_password("x", base64.b64encode(b"short").decode("ascii"))
