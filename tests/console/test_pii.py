"""PII-redaction defense-in-depth (CAI-RESP-264 condition 4).

The bus is coordination, not data — but redact in-view as defense-in-depth.
"""
from nervous_system.console import pii


def test_redacts_nric():
    out = pii.redact("Donor NRIC is S1234567D please verify")
    assert "S1234567D" not in out
    assert "[redacted" in out.lower()


def test_redacts_email():
    out = pii.redact("contact musa.bagushair@gmail.com about it")
    assert "musa.bagushair@gmail.com" not in out


def test_redacts_long_digit_runs():
    out = pii.redact("card 4111111111111111 charged")
    assert "4111111111111111" not in out


def test_passes_through_clean_text():
    text = "build plan ready for ihsanos lane, P2 priority"
    assert pii.redact(text) == text


def test_handles_none_and_empty():
    assert pii.redact(None) is None
    assert pii.redact("") == ""


def test_redact_message_row_redacts_body_and_subject():
    row = {
        "id": 1,
        "body": "NRIC S1234567D and email a@b.com",
        "subject": "re: a@b.com",
        "from_agent": "cc-ihsanos",
    }
    out = pii.redact_message_row(row)
    assert "S1234567D" not in out["body"]
    assert "a@b.com" not in out["body"]
    assert "a@b.com" not in out["subject"]
    # Non-PII fields untouched.
    assert out["from_agent"] == "cc-ihsanos"
    assert out["id"] == 1
