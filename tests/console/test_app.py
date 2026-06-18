"""App-level integration: routing + auth gate + read-only API shape.

Spins up the real stdlib threaded server on an ephemeral port and drives it with
httpx. The DB layer is monkeypatched so tests are hermetic (no live Supabase).
"""
import threading
import time

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("CONSOLE_TOKEN", "test-console-token")
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
