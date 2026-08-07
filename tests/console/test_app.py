"""App-level integration: routing + auth gate + read-only API shape.

Spins up the real stdlib threaded server on an ephemeral port and drives it with
httpx. The DB layer is monkeypatched so tests are hermetic (no live Supabase).
"""
import threading
import time
from unittest.mock import patch

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db


@pytest.fixture
def server(monkeypatch, tmp_path):
    # The test client always connects from loopback (127.0.0.1), so the
    # allowlist here deliberately does NOT include it — that forces every
    # request through the breakglass path below, exercising the
    # Bearer-token-shaped tests exactly like the old CONSOLE_TOKEN did.
    # test_loopback_allowlisted_ip_needs_no_token (below) separately proves
    # the real IP-allowlist path end to end.
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "test-console-token")
    # Isolate the access-audit log: without this it defaults to the real repo's
    # logs/console_access.log — which, since the LIVE console also runs from
    # this same checkout, means test runs silently write fake entries into
    # the production audit trail (found live 2026-07-04, mixed in with real
    # phone traffic).
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    # Hermetic DB stubs.
    monkeypatch.setattr(
        db, "fetch_messages",
        lambda limit=50, thread=None, agent=None: [
            {"id": 1, "thread_id": "t1", "from_agent": "cc-cai",
             "to_agent": "cc-orchestrator", "message_type": "ruling",
             "subject": "hi", "body": "email a@b.com here", "priority": "P2",
             "requires_response": False, "created_at": "2026-06-17T00:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        db, "fetch_lanes",
        lambda: [
            {"agent_id": "cc-ihsanos-1", "base_agent_id": "cc-ihsanos",
             "status": "working", "current_task": "build",
             "heartbeat_age_s": 12, "desired_state": "up",
             "display_name": "Ihsanos lane"},
        ],
    )
    srv = console_app.make_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    # Wait for liveness.
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base
    srv.shutdown()


def H(tok="test-console-token"):
    return {"Authorization": f"Bearer {tok}"}


def test_healthz_open_no_auth(server):
    r = httpx.get(server + "/healthz", timeout=5)
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_api_requires_auth(server):
    r = httpx.get(server + "/api/messages", timeout=5)
    assert r.status_code == 401


def test_api_rejects_token_in_query(server):
    r = httpx.get(server + "/api/messages?token=test-console-token", timeout=5)
    assert r.status_code == 401


def test_api_messages_with_valid_token(server):
    r = httpx.get(server + "/api/messages", headers=H(), timeout=5)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert rows[0]["id"] == 1
    # PII redaction applied in-view.
    assert "a@b.com" not in rows[0]["body"]


def test_api_messages_wrong_token_401(server):
    r = httpx.get(server + "/api/messages", headers=H("nope"), timeout=5)
    assert r.status_code == 401


def test_api_lanes(server):
    r = httpx.get(server + "/api/lanes", headers=H(), timeout=5)
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["base_agent_id"] == "cc-ihsanos"
    assert rows[0]["heartbeat_age_s"] == 12


def test_no_service_key_in_any_response(server):
    for path in ("/healthz", "/api/messages", "/api/lanes"):
        r = httpx.get(server + path, headers=H(), timeout=5)
        assert "SUPABASE_SERVICE_KEY" not in r.text
        assert "service_role" not in r.text.lower()


def test_static_index_served(server):
    r = httpx.get(server + "/", timeout=5)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_x_forwarded_for_spoof_does_not_grant_access(server):
    """Regression test for the exact bug this change removes: the test
    client's real peer IP (127.0.0.1) is never in the allowlist here, and a
    spoofed X-Forwarded-For claiming to BE an allowed IP must not matter —
    prove the server ignores that header entirely for auth."""
    r = httpx.get(
        server + "/api/messages",
        headers={"X-Forwarded-For": "203.0.113.9"},
        timeout=5,
    )
    assert r.status_code == 401


@pytest.fixture
def server_ip_allowed(monkeypatch, tmp_path):
    """The real IP-allowlist path, end to end: the test client connects from
    127.0.0.1, and 127.0.0.1 IS the allowlist — no token/header needed at all."""
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "127.0.0.1")
    monkeypatch.delenv("CONSOLE_BREAKGLASS_TOKEN", raising=False)
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    monkeypatch.setattr(
        db, "fetch_messages", lambda limit=50, thread=None, agent=None: []
    )
    srv = console_app.make_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base
    srv.shutdown()


def test_loopback_allowlisted_ip_needs_no_token(server_ip_allowed):
    r = httpx.get(server_ip_allowed + "/api/messages", timeout=5)
    assert r.status_code == 200


def test_pane_endpoint_requires_auth(server):
    """Same gate as everything else under /api/* — no new exposure."""
    r = httpx.get(server + "/api/lanes/cosem-tdu/pane", timeout=5)
    assert r.status_code == 401


def test_pane_endpoint_404s_for_a_non_live_session(server):
    with patch("nervous_system.console.panes.live_sessions", return_value=["orch"]):
        r = httpx.get(server + "/api/lanes/not-a-real-session/pane", headers=H(), timeout=5)
    assert r.status_code == 404


def test_pane_endpoint_returns_captured_text_for_a_live_session(server):
    with patch("nervous_system.console.panes.live_sessions", return_value=["cosem-tdu"]), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        # Blank-separated so the pane reflow keeps them as two distinct logical
        # lines (consecutive non-blank lines are reflowed into one soft-wrapped run).
        mock_run.return_value.stdout = "line1\n\nline2\n"
        r = httpx.get(server + "/api/lanes/cosem-tdu/pane", headers=H(), timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["session"] == "cosem-tdu"
    assert body["text"] == "line1\nline2"
    # read-only: capture-pane only, never send-keys or any other subcommand
    call_args = mock_run.call_args[0][0]
    assert call_args[1] == "capture-pane"


def test_pane_endpoint_rejects_crafted_session_name_without_shelling_out(server):
    """A session name isn't in live_sessions() -> 404 before any subprocess
    call, regardless of what characters it contains."""
    with patch("nervous_system.console.panes.live_sessions", return_value=["cosem-tdu"]), \
         patch("subprocess.run") as mock_run:
        r = httpx.get(server + "/api/lanes/%3B%20rm%20-rf%20%2F/pane", headers=H(), timeout=5)
    assert r.status_code == 404
    mock_run.assert_not_called()


def test_manifest_served_unauthenticated(server):
    """PWA regression (orch probe, 2026-07-03): the manifest and SW must be
    reachable pre-auth, or the browser can never install/register them even
    though the app otherwise renders fine."""
    r = httpx.get(server + "/manifest.json", timeout=5)
    assert r.status_code == 200
    assert r.headers.get("content-type") == "application/manifest+json"
    body = r.json()
    assert body["name"] == "Fleet Console"
    assert body["display"] == "standalone"


def test_service_worker_served_unauthenticated(server):
    r = httpx.get(server + "/sw.js", timeout=5)
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    assert r.headers.get("cache-control") == "no-cache"
    assert "skipWaiting" in r.text
    assert "clients.claim" in r.text


def test_icons_served_unauthenticated(server):
    r = httpx.get(server + "/static/icons/icon-192.png", timeout=5)
    assert r.status_code == 200
    assert r.headers.get("content-type") == "image/png"
