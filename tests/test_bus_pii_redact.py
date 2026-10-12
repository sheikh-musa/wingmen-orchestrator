"""test_bus_pii_redact.py — scripts/lib/bus_pii_redact.py (hc#359, bus #64729,
fable-audit E-client-lanes-quality.md item 5; phone shapes + HMAC tokens
added per orch-console bus #64805).
"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.bus_pii_redact import redact_pii  # noqa: E402


@pytest.fixture(autouse=True)
def _hmac_key(monkeypatch):
    # Deterministic, non-secret test key -- real deploys set this from the
    # environment and never commit the value.
    monkeypatch.setenv("BUS_PII_HMAC_KEY", "test-only-key-not-a-real-secret")


def test_emirates_id_is_hashed():
    body = "Trainer EID is 784-1988-1234567-1, flagged by cc-cosem-adcda."
    out, counts = redact_pii(body)
    assert "784-1988-1234567-1" not in out
    assert counts["emirates_id"] == 1
    assert "[REDACTED:emirates_id:" in out


def test_nric_is_hashed():
    body = "Candidate NRIC S1234567D failed the upload validation step today."
    out, counts = redact_pii(body)
    assert "S1234567D" not in out
    assert counts["nric"] == 1


def test_three_token_arabic_name_is_hashed():
    body = "Roster shows Ahmad bin Yusof as the trainee missing a photo upload."
    out, counts = redact_pii(body)
    assert "Ahmad bin Yusof" not in out
    assert counts["name"] == 1


def test_non_system_email_is_hashed():
    body = "Client contact is fazlie.rahman@tdu-example-client.com per the thread."
    out, counts = redact_pii(body)
    assert "fazlie.rahman@tdu-example-client.com" not in out
    assert counts["email"] == 1


def test_fleet_domain_email_is_kept():
    body = "CI bot posted from noreply@github.com about the failed run."
    out, counts = redact_pii(body)
    assert "noreply@github.com" in out
    assert "email" not in counts


def test_operator_own_email_is_kept():
    body = "Forward this update to musa.bagushair@gmail.com when ready."
    out, counts = redact_pii(body)
    assert "musa.bagushair@gmail.com" in out
    assert "email" not in counts


def test_clean_body_is_unchanged():
    body = "Status: all three lanes green, no blockers, deploying in ten minutes."
    out, counts = redact_pii(body)
    assert out == body
    assert counts == {}


def test_same_value_hashes_to_the_same_token():
    body1 = "First mention: 784-1988-1234567-1 in thread one."
    body2 = "Second mention: 784-1988-1234567-1 in thread two."
    out1, _ = redact_pii(body1)
    out2, _ = redact_pii(body2)
    token1 = out1[out1.index("[REDACTED:"):]
    token2 = out2[out2.index("[REDACTED:"):]
    assert token1.split("]")[0] == token2.split("]")[0]


def test_redaction_is_idempotent():
    body = "EID 784-1988-1234567-1 on file."
    once, _ = redact_pii(body)
    twice, counts2 = redact_pii(once)
    assert once == twice
    assert counts2 == {}


def test_emirates_id_no_separators_is_hashed():
    body = "Second mention, no dashes: 784199012345671 on the form."
    out, counts = redact_pii(body)
    assert "784199012345671" not in out
    assert counts["emirates_id"] == 1


def test_emirates_id_space_separators_is_hashed():
    body = "Mentioned again as 784 1990 1234567 1 on the scan."
    out, counts = redact_pii(body)
    assert "784 1990 1234567 1" not in out
    assert counts["emirates_id"] == 1


def test_uae_local_phone_is_hashed():
    body = "Reach the trainer at 0564863069 before the session."
    out, counts = redact_pii(body)
    assert "0564863069" not in out
    assert counts["phone"] == 1


def test_uae_intl_plus_and_00_prefix_are_both_hashed():
    out1, counts1 = redact_pii("Call +971 56 486 3069 today.")
    out2, counts2 = redact_pii("Call 00971564863069 today.")
    assert "971 56 486 3069" not in out1
    assert "00971564863069" not in out2
    assert counts1["phone"] == 1
    assert counts2["phone"] == 1


def test_sg_mobile_and_intl_are_hashed():
    out1, counts1 = redact_pii("Ping 91234567 when you land.")
    out2, counts2 = redact_pii("Ping +65 9123 4567 when you land.")
    assert counts1["phone"] == 1
    assert counts2["phone"] == 1


def test_phone_does_not_fire_on_unrelated_ids():
    body = "Thread id abc-123 and bus message #64805 reference op#29494."
    out, counts = redact_pii(body)
    assert out == body
    assert "phone" not in counts


def test_token_is_hmac_not_plain_sha256_bruteforce_resistant():
    value = "784-1988-1234567-1"
    body = f"EID {value} on file."
    out, _ = redact_pii(body)
    token = out[out.index("[REDACTED:"):].split("]")[0]
    plain_sha256_10 = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    assert plain_sha256_10 not in token


def test_missing_hmac_key_falls_back_to_bare_marker_not_plain_hash(monkeypatch):
    monkeypatch.delenv("BUS_PII_HMAC_KEY", raising=False)
    value = "784-1988-1234567-1"
    body = f"EID {value} on file."
    out, counts = redact_pii(body)
    assert value not in out
    assert counts["emirates_id"] == 1
    assert "[REDACTED:emirates_id]" in out
    plain_sha256_10 = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    assert plain_sha256_10 not in out
