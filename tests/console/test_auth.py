"""Auth middleware tests (CAI-RESP-264 condition 3).

Token from CONSOLE_TOKEN; Authorization: Bearer ONLY (reject URL/query);
fail-closed 401; audit-log every access (who/when/path/outcome).
"""
import os

import pytest

from nervous_system.console import auth


def test_missing_token_env_fails_closed(monkeypatch):
    monkeypatch.delenv("CONSOLE_TOKEN", raising=False)
    # No configured token -> every request rejected (fail-closed).
    assert auth.check_bearer({"Authorization": "Bearer whatever"}, query={}) is False


def test_valid_bearer_header_accepts(monkeypatch):
    monkeypatch.setenv("CONSOLE_TOKEN", "s3cret-token")
    assert auth.check_bearer({"Authorization": "Bearer s3cret-token"}, query={}) is True


def test_wrong_bearer_rejected(monkeypatch):
    monkeypatch.setenv("CONSOLE_TOKEN", "s3cret-token")
    assert auth.check_bearer({"Authorization": "Bearer nope"}, query={}) is False


def test_missing_header_rejected(monkeypatch):
    monkeypatch.setenv("CONSOLE_TOKEN", "s3cret-token")
    assert auth.check_bearer({}, query={}) is False


def test_token_in_query_rejected(monkeypatch):
    """Token-in-URL/query must NEVER authenticate (header-only)."""
    monkeypatch.setenv("CONSOLE_TOKEN", "s3cret-token")
    assert auth.check_bearer({}, query={"token": "s3cret-token"}) is False
    # Even with a (wrong) header present, the query token cannot rescue it.
    assert (
        auth.check_bearer({"Authorization": "Bearer wrong"}, query={"token": "s3cret-token"})
        is False
    )


def test_non_bearer_scheme_rejected(monkeypatch):
    monkeypatch.setenv("CONSOLE_TOKEN", "s3cret-token")
    assert auth.check_bearer({"Authorization": "Basic s3cret-token"}, query={}) is False


def test_case_insensitive_header_name(monkeypatch):
    monkeypatch.setenv("CONSOLE_TOKEN", "s3cret-token")
    assert auth.check_bearer({"authorization": "Bearer s3cret-token"}, query={}) is True


def test_empty_token_env_fails_closed(monkeypatch):
    monkeypatch.setenv("CONSOLE_TOKEN", "")
    assert auth.check_bearer({"Authorization": "Bearer "}, query={}) is False


def test_audit_log_writes_outcome(monkeypatch, tmp_path):
    log_path = tmp_path / "console_access.log"
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(log_path))
    auth.audit("203.0.113.5", "/api/messages", "200")
    auth.audit("203.0.113.5", "/api/lanes", "401")
    content = log_path.read_text()
    assert "/api/messages" in content
    assert "203.0.113.5" in content
    assert "200" in content
    assert "401" in content
    # Two lines, one per access.
    assert len([ln for ln in content.splitlines() if ln.strip()]) == 2


def test_audit_log_does_not_leak_token(monkeypatch, tmp_path):
    log_path = tmp_path / "console_access.log"
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(log_path))
    monkeypatch.setenv("CONSOLE_TOKEN", "s3cret-token")
    auth.audit("1.2.3.4", "/api/messages", "200")
    assert "s3cret-token" not in log_path.read_text()
