"""End-to-end SSE delivery: a feeder-published row reaches a streaming client.

Drives the real threaded server with a stubbed feed source (no live Supabase),
proving the /api/stream wiring delivers new bus rows and is auth-gated.
"""
import threading
import time

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db


@pytest.fixture
def stream_server(monkeypatch):
    # Loopback (the test client's real peer) is deliberately NOT allowlisted
    # so these requests exercise the breakglass path, same shape as the old
    # CONSOLE_TOKEN.
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "stream-token")

    # A growing fake bus: the feeder calls _fetch_since via db._query.
    state = {"next_id": 1}

    def fake_query(sql, params):
        lowered = sql.lower()
        if "id > %s" in lowered:  # feeder incremental fetch
            last_id = params[0]
            rows = []
            # Emit one new row per poll once the test injects it.
            if state.get("emit") and state["next_id"] > last_id:
                rows.append({
                    "id": state["next_id"], "thread_id": "t", "from_agent": "cc-cai",
                    "to_agent": "cc-orchestrator", "message_type": "ruling",
                    "subject": "live", "body": "no pii here", "priority": "P1",
                    "requires_response": True, "sub_tag": None,
                    "created_at": "2026-06-17T00:00:00Z",
                })
                state["emit"] = False
            return rows
        return []  # baseline fetch

    monkeypatch.setattr(db, "_query", fake_query)
    monkeypatch.setattr(db, "fetch_messages", lambda **k: [])

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
    yield base, state
    srv.shutdown()


def test_stream_requires_auth(stream_server):
    base, _ = stream_server
    r = httpx.get(base + "/api/stream", timeout=3)
    assert r.status_code == 401


def test_stream_delivers_new_row(stream_server):
    base, state = stream_server
    received = []
    with httpx.stream(
        "GET", base + "/api/stream",
        headers={"Authorization": "Bearer stream-token"}, timeout=10,
    ) as resp:
        assert resp.status_code == 200
        # Trigger an emit; the feeder polls ~1s.
        state["next_id"] = 5
        state["emit"] = True
        deadline = time.time() + 8
        buf = ""
        for text in resp.iter_text():
            buf += text
            if "data:" in buf:
                received.append(buf)
                break
            if time.time() > deadline:
                break
    assert received, "no SSE data frame received"
    assert '"id": 5' in received[0]
