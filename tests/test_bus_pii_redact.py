"""test_bus_pii_redact.py — scripts/lib/bus_pii_redact.py (hc#359, bus #64729,
fable-audit E-client-lanes-quality.md item 5).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.bus_pii_redact import redact_pii  # noqa: E402


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
