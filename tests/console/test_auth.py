"""Access-control tests: Tailscale-IP allowlist + dormant breakglass token.

Replaces the old CONSOLE_TOKEN password model (CAI-RESP-264 condition 1,
IP-allowlist variant). Peer IP is the primary gate; the breakglass token is
header-only (Authorization: Bearer), dormant, and logged loudly when used.
Fail-closed on an empty allowlist; audit-log every access (who/when/path/outcome).
"""
from nervous_system.console import auth


def test_empty_allowlist_fails_closed_even_for_any_ip(monkeypatch):
    monkeypatch.delenv("CONSOLE_ALLOWED_IPS", raising=False)
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    authed, method = auth.check_access("100.104.36.27", {})
    assert authed is False
    assert method == "denied"


def test_allowlisted_ip_authorizes_with_no_token_at_all(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.126.219.100,100.104.36.27")
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    authed, method = auth.check_access("100.104.36.27", {})
    assert authed is True
    assert method == "ip"


def test_allowlist_whitespace_and_trailing_commas_tolerated(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", " 100.104.36.27 , ,100.104.193.6,")
    authed, method = auth.check_access("100.104.193.6", {})
    assert authed is True
    assert method == "ip"


def test_non_allowlisted_ip_denied_with_no_breakglass_configured(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    authed, method = auth.check_access("203.0.113.9", {})
    assert authed is False
    assert method == "denied"


def test_x_forwarded_for_never_substitutes_for_the_real_peer_ip(monkeypatch):
    """The core security property this change exists for: spoofing XFF must
    NOT grant access. If this ever fails, someone reintroduced the XFF-trust
    bug (worse than the password it replaced)."""
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    # peer_ip is the untrusted caller (off-tailnet attacker); the header
    # spoofs an allowed IP. check_access must ignore the header entirely.
    authed, method = auth.check_access(
        "203.0.113.9", {"X-Forwarded-For": "100.104.36.27"}
    )
    assert authed is False
    assert method == "denied"


def test_breakglass_token_recovers_a_non_allowlisted_peer(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "brk-s3cret")
    authed, method = auth.check_access(
        "203.0.113.9", {"Authorization": "Bearer brk-s3cret"}
    )
    assert authed is True
    assert method == "breakglass"


def test_breakglass_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "brk-s3cret")
    authed, method = auth.check_access(
        "203.0.113.9", {"Authorization": "Bearer nope"}
    )
    assert authed is False
    assert method == "denied"


def test_breakglass_rejects_non_bearer_scheme(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "brk-s3cret")
    authed, _ = auth.check_access(
        "203.0.113.9", {"Authorization": "Basic brk-s3cret"}
    )
    assert authed is False


def test_breakglass_case_insensitive_header_name(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "brk-s3cret")
    authed, method = auth.check_access(
        "203.0.113.9", {"authorization": "Bearer brk-s3cret"}
    )
    assert authed is True
    assert method == "breakglass"


def test_dormant_breakglass_unset_never_activates(monkeypatch):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    authed, method = auth.check_access(
        "203.0.113.9", {"Authorization": "Bearer anything"}
    )
    assert authed is False
    assert method == "denied"


def test_breakglass_use_is_logged_loudly_and_separately(monkeypatch, tmp_path):
    log_path = tmp_path / "console_access.log"
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(log_path))
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "100.104.36.27")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "brk-s3cret")
    auth.check_access("203.0.113.9", {"Authorization": "Bearer brk-s3cret"})
    content = log_path.read_text()
    assert "BREAKGLASS" in content
    assert "203.0.113.9" in content


def test_audit_log_writes_outcome(monkeypatch, tmp_path):
    log_path = tmp_path / "console_access.log"
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(log_path))
    auth.audit("100.104.36.27", "/api/messages", "200")
    auth.audit("203.0.113.9", "/api/lanes", "401")
    content = log_path.read_text()
    assert "/api/messages" in content
    assert "100.104.36.27" in content
    assert "200" in content
    assert "401" in content
    # Two lines, one per access.
    assert len([ln for ln in content.splitlines() if ln.strip()]) == 2


def test_audit_log_does_not_leak_breakglass_token(monkeypatch, tmp_path):
    log_path = tmp_path / "console_access.log"
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(log_path))
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "brk-s3cret")
    auth.audit("1.2.3.4", "/api/messages", "200")
    assert "brk-s3cret" not in log_path.read_text()
