"""Tests for nervous_system.secret_redact — the outbound secret scrubber.

Covers the two newly-added patterns (pg DSN, Telegram bot token), the inherited
patterns, byte-identical passthrough of clean text, and the stdin→stdout CLI the
shell send scripts pipe through.
"""
import subprocess
import sys
from pathlib import Path

from nervous_system.secret_redact import PATTERNS, redact

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pg_dsn_redacted():
    text = "connect via postgres://appuser:s3cr3tP%40ss@db.example.com:5432/prod now"
    out = redact(text)
    assert "s3cr3tP%40ss" not in out
    assert "appuser" not in out
    assert "[REDACTED:pg-dsn]" in out
    # legible surrounding context preserved
    assert out.startswith("connect via ")
    assert out.endswith(" now")


def test_pg_dsn_postgresql_scheme():
    text = "postgresql://u:p@host/db"
    out = redact(text)
    assert out == "[REDACTED:pg-dsn]"


def test_telegram_bot_token_redacted():
    token = "123456789:AAH8xY_z-QwErTyUiOpAsDfGhesk0123456789"
    text = f"bot token is {token} keep it safe"
    out = redact(text)
    assert token not in out
    assert "[REDACTED:tg-token]" in out
    assert out == "bot token is [REDACTED:tg-token] keep it safe"


def test_clean_text_byte_identical():
    clean = (
        "Deploy finished for ihsanos. 3 files changed, 0 errors.\n"
        "Next: verify the live storefront at https://example.com/shop\n"
        "Ratio 5:30, cost $12.40 — all good."
    )
    assert redact(clean) == clean


def test_empty_text_passthrough():
    assert redact("") == ""


def test_anthropic_and_jwt_and_gh_still_redacted():
    text = (
        "key sk-ant-abc123_XYZ and jwt "
        "eyJhbGc.eyJzdWIiA.sIgn-tur3 and gh ghp_deadBEEF00 done"
    )
    out = redact(text)
    assert "sk-ant-abc123_XYZ" not in out
    assert "ghp_deadBEEF00" not in out
    assert "[REDACTED:anthropic-key]" in out
    assert "[REDACTED:jwt]" in out
    assert "[REDACTED:gh-token]" in out


def test_patterns_list_exposed():
    # module contract: pattern list is importable
    assert len(PATTERNS) >= 7
    for pat, marker in PATTERNS:
        assert marker.startswith("[REDACTED")


def test_cli_stdin_stdout_redacts():
    proc = subprocess.run(
        [sys.executable, "-m", "nervous_system.secret_redact"],
        input="dsn postgres://u:pw@h:5432/d end",
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0
    assert proc.stdout == "dsn [REDACTED:pg-dsn] end"


def test_cli_clean_text_byte_identical_no_added_newline():
    clean = "hello operator, no secrets here"
    proc = subprocess.run(
        [sys.executable, "-m", "nervous_system.secret_redact"],
        input=clean, capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0
    assert proc.stdout == clean  # no trailing newline added, unchanged
