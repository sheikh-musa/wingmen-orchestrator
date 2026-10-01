"""GLM Coding Plan (z.ai) usage card (op#24597).

Locks:
  * a good quota read renders numbers (backend payload + the fleet.js card);
  * a failed read -> "GLM usage unavailable", never stale numbers, never a guess;
  * the 5-min server cache (z.ai / vault not hammered by page loads);
  * the key NEVER appears in any response body or log line (fake key, mocked HTTP).
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import httpx
import pytest

from nervous_system.console import app as console_app
from nervous_system.console import db, glm_usage

FAKE_KEY = "fake-glm-key-0123456789abcdef.SENTINEL"
RESET_5H_MS = 1_790_000_000_000
RESET_WK_MS = 1_790_500_000_000

QUOTA_OK = {
    "code": 200, "msg": "ok", "success": True,
    "data": {"level": "pro", "limits": [
        {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 60000,
         "currentValue": 1650, "remaining": 58349, "percentage": 2,
         "nextResetTime": RESET_WK_MS},
        {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 12000,
         "currentValue": 1650, "remaining": 10349, "percentage": 13,
         "nextResetTime": RESET_5H_MS},
    ]},
}

ROOT = Path(__file__).resolve().parents[2]


def _fake_http(calls, body=QUOTA_OK):
    def _get(url, key):
        calls.append((url, key))
        return json.loads(json.dumps(body))
    return _get


@pytest.fixture
def fake_key(monkeypatch):
    reads = []

    def _read():
        reads.append(1)
        return FAKE_KEY
    monkeypatch.setattr(glm_usage, "read_key", _read)
    return reads


# ---- backend ---------------------------------------------------------------

def test_good_read_returns_numbers(fake_key, monkeypatch):
    calls = []
    monkeypatch.setattr(glm_usage, "http_get_json", _fake_http(calls))
    out = glm_usage.get_glm_usage(now=1000.0)
    assert calls == [(glm_usage.QUOTA_URL, FAKE_KEY)]
    assert out["available"] is True and out["level"] == "pro"
    w5, wk = out["windows"]   # shortest window first, whatever the wire order
    assert (w5["label"], w5["used"], w5["cap"], w5["remaining"], w5["pct"]) == ("5h", 1650, 12000, 10349, 13.8)
    assert (wk["label"], wk["used"], wk["cap"], wk["pct"]) == ("wk", 1650, 60000, 2.8)
    assert w5["resets_at"].endswith("+00:00")
    assert FAKE_KEY not in json.dumps(out)


def test_cache_reuses_good_read_for_five_minutes(fake_key, monkeypatch):
    calls = []
    monkeypatch.setattr(glm_usage, "http_get_json", _fake_http(calls))
    glm_usage.get_glm_usage(now=1000.0)
    again = glm_usage.get_glm_usage(now=1000.0 + glm_usage.CACHE_TTL_S - 1)
    assert len(calls) == 1 and len(fake_key) == 1
    assert again["available"] and again["age_s"] == glm_usage.CACHE_TTL_S - 1
    glm_usage.get_glm_usage(now=1000.0 + glm_usage.CACHE_TTL_S + 1)
    assert len(calls) == 2 and len(fake_key) == 1      # key held in memory; vault read once


def test_failed_refresh_never_serves_stale_numbers(fake_key, monkeypatch):
    monkeypatch.setattr(glm_usage, "http_get_json", _fake_http([]))
    assert glm_usage.get_glm_usage(now=1000.0)["available"] is True

    def _boom(url, key):
        raise glm_usage.GlmUsageError("z.ai quota HTTP 500")
    monkeypatch.setattr(glm_usage, "http_get_json", _boom)
    out = glm_usage.get_glm_usage(now=1000.0 + glm_usage.CACHE_TTL_S + 1)
    assert out == {"available": False, "level": None, "windows": [], "age_s": None}


@pytest.mark.parametrize("body", [
    {"code": 401, "msg": "unauthorized", "data": None},
    {"code": 200, "data": {"limits": []}},
    {"code": 200, "data": {"limits": [{"type": "CREDIT_LIMIT", "usage": 0, "currentValue": 1}]}},
    "not json",
])
def test_malformed_or_error_body_is_unavailable(fake_key, monkeypatch, body):
    monkeypatch.setattr(glm_usage, "http_get_json", lambda u, k: body)
    assert glm_usage.get_glm_usage(now=5.0)["available"] is False


def test_vault_failure_is_unavailable(monkeypatch):
    def _vault_down():
        raise RuntimeError("vault: DATABASE_URL not set")
    monkeypatch.setattr(glm_usage, "read_key", _vault_down)
    assert glm_usage.get_glm_usage(now=5.0)["available"] is False


def test_401_drops_cached_key_so_a_rotation_is_picked_up(fake_key, monkeypatch):
    def _401(url, key):
        raise glm_usage.GlmUsageError("z.ai quota HTTP 401")
    monkeypatch.setattr(glm_usage, "http_get_json", _401)
    glm_usage.get_glm_usage(now=0.0)
    glm_usage.get_glm_usage(now=glm_usage.FAIL_TTL_S + 1.0)
    assert len(fake_key) == 2


def test_real_http_error_paths_never_leak_key(monkeypatch):
    """The real urllib caller: errors carry status / class name only, never the key."""
    import urllib.error
    import urllib.request

    def _raise_http(req, timeout):
        assert req.get_header("Authorization") == FAKE_KEY   # raw key, no "Bearer"
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden " + FAKE_KEY, {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", _raise_http)
    with pytest.raises(glm_usage.GlmUsageError) as ei:
        glm_usage._http_get_json(glm_usage.QUOTA_URL, FAKE_KEY)
    assert FAKE_KEY not in str(ei.value) and ei.value.__cause__ is None

    def _raise_net(req, timeout):
        raise OSError("connect failed with " + FAKE_KEY)
    monkeypatch.setattr(urllib.request, "urlopen", _raise_net)
    with pytest.raises(glm_usage.GlmUsageError) as ei:
        glm_usage._http_get_json(glm_usage.QUOTA_URL, FAKE_KEY)
    assert FAKE_KEY not in str(ei.value)


def test_unexpected_exception_logs_class_name_only(monkeypatch, caplog):
    monkeypatch.setattr(glm_usage, "read_key", lambda: FAKE_KEY)

    def _leaky(url, key):
        raise ValueError("bad thing near " + key)
    monkeypatch.setattr(glm_usage, "http_get_json", _leaky)
    with caplog.at_level(logging.DEBUG):
        out = glm_usage.get_glm_usage(now=1.0)
    assert out["available"] is False
    assert caplog.records, "a failure must be logged"
    assert all(FAKE_KEY not in r.getMessage() for r in caplog.records)


def test_vault_read_pins_file_first_dsn_and_restores_environment(monkeypatch):
    seen = {}

    class _V:
        def get(self, name, reason):
            seen["dsn"] = os.environ.get("DATABASE_URL")
            seen["agent"] = os.environ.get("AGENT_ID")
            seen["name"] = name
            return types.SimpleNamespace(value=FAKE_KEY, leak_flagged=False, leak_reason=None)
    monkeypatch.setitem(sys.modules, "nervous_system.vault", types.SimpleNamespace(vault=_V()))
    import scripts.lib.substrate_dsn as sd
    monkeypatch.setattr(sd, "dsn_from_env_file", lambda: "postgresql://file-first.invalid/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://stale-inherited.invalid/db")
    monkeypatch.delenv("AGENT_ID", raising=False)
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    assert glm_usage._read_key_from_vault() == FAKE_KEY
    assert seen == {"dsn": "postgresql://file-first.invalid/db", "agent": "fleet-console",
                    "name": "GLM_CODING_KEY"}
    assert os.environ["DATABASE_URL"] == "postgresql://stale-inherited.invalid/db"
    assert "AGENT_ID" not in os.environ


def _fake_vault(monkeypatch, leak_flagged):
    class _V:
        def get(self, name, reason):
            return types.SimpleNamespace(value=FAKE_KEY, leak_flagged=leak_flagged,
                                         leak_reason="x" if leak_flagged else None)
    monkeypatch.setitem(sys.modules, "nervous_system.vault", types.SimpleNamespace(vault=_V()))
    import scripts.lib.substrate_dsn as sd
    monkeypatch.setattr(sd, "dsn_from_env_file", lambda: "postgresql://file-first.invalid/db")
    # conftest stubs read_key hermetically; route through the REAL vault reader
    # (against the fake vault above) so the leak-flag branch is exercised.
    monkeypatch.setattr(glm_usage, "read_key", glm_usage._read_key_from_vault)


def test_vault_leak_flagged_key_is_refused_when_not_accepted(monkeypatch):
    _fake_vault(monkeypatch, leak_flagged=True)
    monkeypatch.setattr(glm_usage, "ACCEPTED_LEAK_FLAG", {})
    with pytest.raises(glm_usage.GlmUsageError) as ei:
        glm_usage._read_key_from_vault()
    assert FAKE_KEY not in str(ei.value)
    assert glm_usage._key_warning is None


def test_accepted_leak_flag_is_name_scoped_and_attributed():
    # The override is a code constant for exactly this key, attributed to the operator ruling.
    assert set(glm_usage.ACCEPTED_LEAK_FLAG) == {"GLM_CODING_KEY"}
    assert "op#24626" in glm_usage.ACCEPTED_LEAK_FLAG["GLM_CODING_KEY"]
    assert "Musa" in glm_usage.ACCEPTED_LEAK_FLAG["GLM_CODING_KEY"]
    with pytest.raises(TypeError):
        glm_usage.ACCEPTED_LEAK_FLAG["OTHER_KEY"] = "x"   # read-only mapping


def test_flagged_and_accepted_key_is_used_and_payload_warns(monkeypatch, caplog):
    _fake_vault(monkeypatch, leak_flagged=True)
    calls = []
    monkeypatch.setattr(glm_usage, "http_get_json", _fake_http(calls))
    with caplog.at_level(logging.DEBUG):
        out = glm_usage.get_glm_usage(now=1000.0)
    assert calls == [(glm_usage.QUOTA_URL, FAKE_KEY)]
    assert out["available"] is True
    assert out["key_warning"] == glm_usage.ACCEPTED_LEAK_WARNING
    assert "op#24626" in out["key_warning"] and "leak-flagged" in out["key_warning"]
    assert FAKE_KEY not in json.dumps(out)
    assert all(FAKE_KEY not in r.getMessage() for r in caplog.records)
    # the warning persists on cached reads while the same key is in use
    again = glm_usage.get_glm_usage(now=1000.0 + glm_usage.CACHE_TTL_S + 1)
    assert again["key_warning"] == glm_usage.ACCEPTED_LEAK_WARNING


def test_flagged_not_accepted_is_unavailable_end_to_end(monkeypatch):
    _fake_vault(monkeypatch, leak_flagged=True)
    monkeypatch.setattr(glm_usage, "ACCEPTED_LEAK_FLAG", {})
    calls = []
    monkeypatch.setattr(glm_usage, "http_get_json", _fake_http(calls))
    out = glm_usage.get_glm_usage(now=1000.0)
    assert out == {"available": False, "level": None, "windows": [], "age_s": None}
    assert calls == []          # the flagged key never reached z.ai


def test_unflagged_key_has_no_warning(monkeypatch):
    _fake_vault(monkeypatch, leak_flagged=False)
    monkeypatch.setattr(glm_usage, "http_get_json", _fake_http([]))
    out = glm_usage.get_glm_usage(now=1000.0)
    assert out["available"] is True and out["key_warning"] is None


def test_glm_error_log_carries_exception_class(fake_key, monkeypatch, caplog):
    def _down(url, key):
        raise glm_usage.GlmUsageError("z.ai quota HTTP 500")
    monkeypatch.setattr(glm_usage, "http_get_json", _down)
    with caplog.at_level(logging.DEBUG):
        glm_usage.get_glm_usage(now=1.0)
    assert any("GlmUsageError" in r.getMessage() for r in caplog.records)


# ---- /api/fleet end to end: numbers present, key absent from body + logs -----

@pytest.fixture
def fleet_server(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_ALLOWED_IPS", "203.0.113.9")
    monkeypatch.setenv("CONSOLE_BREAKGLASS_TOKEN", "test-console-token")
    monkeypatch.setenv("CONSOLE_ACCESS_LOG", str(tmp_path / "console_access.log"))
    for name in ("fetch_lanes", "fetch_deploys", "fetch_needs_you", "fetch_coordinators",
                 "fetch_backlog", "fetch_pane_context", "fetch_pool_usage", "fetch_queue",
                 "fetch_inbox_backlog", "fetch_asks"):
        monkeypatch.setattr(db, name, lambda: [])
    monkeypatch.setattr(console_app.panes, "live_sessions", lambda: [])
    monkeypatch.setattr(console_app, "_proc_models", lambda: {})
    srv = console_app.make_server(host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base, tmp_path
    srv.shutdown()


AUTH = {"Authorization": "Bearer test-console-token"}


def test_api_fleet_carries_glm_numbers_and_never_the_key(fleet_server, fake_key, monkeypatch, caplog):
    base, tmp = fleet_server
    monkeypatch.setattr(glm_usage, "http_get_json", _fake_http([]))
    with caplog.at_level(logging.DEBUG):
        r = httpx.get(base + "/api/fleet", headers=AUTH, timeout=10)
    assert r.status_code == 200
    g = r.json()["glm_usage"]
    assert g["available"] is True and [w["label"] for w in g["windows"]] == ["5h", "wk"]
    assert g["windows"][0]["used"] == 1650 and g["windows"][0]["cap"] == 12000
    assert FAKE_KEY not in r.text
    assert all(FAKE_KEY not in rec.getMessage() for rec in caplog.records)
    access = tmp / "console_access.log"
    assert not access.exists() or FAKE_KEY not in access.read_text()


def test_api_fleet_glm_failure_is_unavailable_not_stale(fleet_server, fake_key, monkeypatch, caplog):
    base, _ = fleet_server

    def _down(url, key):
        raise glm_usage.GlmUsageError("z.ai quota fetch failed (URLError)")
    monkeypatch.setattr(glm_usage, "http_get_json", _down)
    with caplog.at_level(logging.DEBUG):
        r = httpx.get(base + "/api/fleet", headers=AUTH, timeout=10)
    g = r.json()["glm_usage"]
    assert g == {"available": False, "level": None, "windows": [], "age_s": None}
    assert FAKE_KEY not in r.text
    assert all(FAKE_KEY not in rec.getMessage() for rec in caplog.records)


# ---- fleet.js card render (node vm sandbox, same idiom as fleet_pace.test.js) --

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_fleet_js_glm_card_renders():
    r = subprocess.run(["node", str(ROOT / "tests" / "console" / "fleet_glm.test.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
