"""Hosted (phone) console ACTION LAYER — fc-v63 (Musa op#20684/20687/20692).

Every hosted action must: require the bearer; validate its target against the
live roster (read-only DB, patched here); refuse singleton-protected targets;
require the typed confirm == target; and be FAIL-CLOSED on a config gap (no
upstream / unmapped host / missing script) — never a local shell-out for a
tmux action. Valid requests are PROXIED verbatim to the lane-host console
(stubbed here) whose own guards then apply.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import httpx
import pytest

from nervous_system.console import hosted_server as hs

BEARER = "phone-bearer-token-at-least-24-chars"
UPTOK = "mini-console-token-xyz"


class _Upstream:
    """Records every request; answers 200 {ok:true} unless a canned reply is set."""
    def __init__(self):
        self.calls = []
        self.reply = (200, {"ok": True})
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                outer.calls.append({"method": self.command, "path": self.path,
                                    "auth": self.headers.get("Authorization"),
                                    "actor": self.headers.get("X-Wingmen-Hosted-Actor"),
                                    "body": json.loads(body) if body else None})
                code, payload = outer.reply
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_POST = _do
            do_GET = _do

            def log_message(self, *a):
                return
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def upstream():
    u = _Upstream()
    yield u
    u.srv.shutdown()


@pytest.fixture
def hosted(monkeypatch, tmp_path, upstream):
    monkeypatch.setenv("CONSOLE_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("CONSOLE_TOKEN", UPTOK)
    monkeypatch.delenv("CONSOLE_UPSTREAM_TOKEN", raising=False)
    monkeypatch.setenv("CONSOLE_UPSTREAM_URL", upstream.url)
    monkeypatch.setenv("CONSOLE_UPSTREAM_HOST", "Sheikhs-Mini")
    monkeypatch.delenv("CONSOLE_UPSTREAM_HOSTS", raising=False)
    monkeypatch.delenv("CONSOLE_KILL", raising=False)
    monkeypatch.setenv("CONSOLE_HOSTED_AUDIT_LOG", str(tmp_path / "hosted_actions.log"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only")
    monkeypatch.setattr(hs, "_db_lane_roster", lambda: {"cosem-tdu", "irsyad-worker-2"})
    monkeypatch.setattr(hs, "_db_lane_host",
                        lambda s: {"cosem-tdu": "Sheikhs-Mini", "irsyad-worker-2": "gzbai",
                                   "nazim": "Sheikhs-Mini", "cai": "Sheikhs-Mini"}.get(s))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hs._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    yield base
    srv.shutdown()


def H(tok=BEARER):
    return {"Authorization": f"Bearer {tok}"}


def _audit_lines(tmp_path):
    p = tmp_path / "hosted_actions.log"
    return p.read_text().splitlines() if p.exists() else []


# ------------------------------------------------------------------ auth + surface
@pytest.mark.parametrize("route,body", [
    ("/api/lane-boot", {"session": "cosem-tdu", "confirm": "cosem-tdu"}),
    ("/api/lane-down", {"session": "cosem-tdu", "confirm": "cosem-tdu"}),
    ("/api/reset", {"body": "nazim", "confirm": "nazim"}),
    ("/api/apply-armed", {"session": "cosem-tdu", "kind": "token", "confirm": "cosem-tdu"}),
    ("/api/assign", {"agent": "cc-cosem-tdu", "ask": "x"}),
    ("/api/ask-close", {"id": 1, "action": "confirm"}),
])
def test_every_action_requires_the_bearer(hosted, upstream, route, body):
    with patch("subprocess.run") as run:
        r = httpx.post(hosted + route, json=body, timeout=5)
        r2 = httpx.post(hosted + route, headers=H("wrong-token-wrong-token-wrong"), json=body, timeout=5)
    assert r.status_code == 401 and r2.status_code == 401
    assert upstream.calls == [] and run.call_count == 0


@pytest.mark.parametrize("route", ["/api/backlog", "/api/switch-token", "/api/switch-all", "/api/set-pointer", "/api/add-token"])
def test_mini_only_mutations_stay_refused(hosted, upstream, route):
    r = httpx.post(hosted + route, headers=H(), json={}, timeout=5)
    assert r.status_code == 403 and upstream.calls == []


def test_kill_switch_503s_actions(hosted, upstream, monkeypatch):
    monkeypatch.setenv("CONSOLE_KILL", "1")
    r = httpx.post(hosted + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 503 and upstream.calls == []


# ------------------------------------------------------------------ lane-boot / lane-down
@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_unknown_target_400_never_proxied(hosted, upstream, route):
    r = httpx.post(hosted + route, headers=H(), json={"session": "ghost", "confirm": "ghost"}, timeout=5)
    assert r.status_code == 400 and r.json()["error"] == "unknown lane"
    r2 = httpx.post(hosted + route, headers=H(), json={"session": "cosem-[client]", "confirm": "cosem-[client]"}, timeout=5)
    assert r2.status_code == 400   # a scrubbed id can never be a target
    assert upstream.calls == []


@pytest.mark.parametrize("target", ["nazim", "cai", "orch", "fleet-health", "quality", "hub", "cc-orchestrator", "cc-cosem-tdu"])
@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_singleton_protected_403_never_proxied(hosted, upstream, route, target):
    r = httpx.post(hosted + route, headers=H(), json={"session": target, "confirm": target}, timeout=5)
    assert r.status_code == 403 and upstream.calls == []


@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_confirm_mismatch_400_never_proxied(hosted, upstream, route):
    r = httpx.post(hosted + route, headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-td"}, timeout=5)
    assert r.status_code == 400 and "confirm" in r.json()["error"] and upstream.calls == []
    r = httpx.post(hosted + route, headers=H(), json={"session": "cosem-tdu"}, timeout=5)
    assert r.status_code == 400 and upstream.calls == []


@pytest.mark.parametrize("route", ["/api/lane-boot", "/api/lane-down"])
def test_lane_action_valid_is_proxied_verbatim_with_upstream_bearer(hosted, upstream, route, tmp_path):
    upstream.reply = (200, {"ok": True, "action": route.split("-")[-1], "session": "cosem-tdu", "tail": "BOOTED"})
    with patch("subprocess.run") as run:
        r = httpx.post(hosted + route, headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 200 and r.json()["ok"] is True
    run.assert_not_called()                                  # NEVER a local shell-out
    assert len(upstream.calls) == 1
    c = upstream.calls[0]
    assert c["method"] == "POST" and c["path"] == route
    assert c["body"] == {"session": "cosem-tdu", "confirm": "cosem-tdu"}
    assert c["auth"] == "Bearer " + UPTOK and c["actor"] == "hosted-console"
    lines = _audit_lines(tmp_path)
    assert any(f"{route}:cosem-tdu@Sheikhs-Mini\tproxy" in ln for ln in lines)
    assert any(f"{route}:cosem-tdu@Sheikhs-Mini\t200" in ln for ln in lines)


def test_lane_action_upstream_refusal_passes_through(hosted, upstream):
    upstream.reply = (409, {"ok": False, "tail": "REFUSED: BUSY"})
    r = httpx.post(hosted + "/api/lane-down", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 409 and "BUSY" in r.json()["tail"]


def test_lane_on_unmapped_host_503_fail_closed(hosted, upstream):
    """irsyad-worker-2 lives on gzbai — no console mapped there -> refuse, never
    route it to the wrong host's console."""
    r = httpx.post(hosted + "/api/lane-boot", headers=H(), json={"session": "irsyad-worker-2", "confirm": "irsyad-worker-2"}, timeout=5)
    assert r.status_code == 503 and "gzbai" in r.json()["error"] and upstream.calls == []


def test_lane_on_mapped_second_host_routes_there(hosted, upstream, monkeypatch):
    other = _Upstream()
    try:
        monkeypatch.setenv("CONSOLE_UPSTREAM_HOSTS", f"gzbai={other.url}")
        r = httpx.post(hosted + "/api/lane-boot", headers=H(), json={"session": "irsyad-worker-2", "confirm": "irsyad-worker-2"}, timeout=5)
        assert r.status_code == 200
        assert len(other.calls) == 1 and other.calls[0]["path"] == "/api/lane-boot"
        assert upstream.calls == []
    finally:
        other.srv.shutdown()


def test_no_upstream_configured_503_never_local(hosted, upstream, monkeypatch):
    monkeypatch.delenv("CONSOLE_UPSTREAM_URL", raising=False)
    with patch("subprocess.run") as run:
        r = httpx.post(hosted + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
        r2 = httpx.post(hosted + "/api/reset", headers=H(), json={"body": "nazim", "confirm": "nazim"}, timeout=5)
    assert r.status_code == 503 and r2.status_code == 503
    run.assert_not_called() and upstream.calls == []


def test_roster_lookup_failure_503_fail_closed(hosted, upstream, monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(hs, "_db_lane_roster", boom)
    r = httpx.post(hosted + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 503 and upstream.calls == []


def test_upstream_unreachable_is_502_not_a_crash(hosted, upstream, monkeypatch):
    monkeypatch.setenv("CONSOLE_UPSTREAM_URL", "http://127.0.0.1:9")   # closed port
    r = httpx.post(hosted + "/api/lane-boot", headers=H(), json={"session": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=10)
    assert r.status_code == 502 and "unreachable" in r.json()["error"]


# ------------------------------------------------------------------ reset (3 singletons)
def test_reset_unknown_body_400_and_confirm_required(hosted, upstream):
    r = httpx.post(hosted + "/api/reset", headers=H(), json={"body": "cosem-tdu", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 400 and r.json()["allowed"] == ["nazim", "cai", "hub"]
    r = httpx.post(hosted + "/api/reset", headers=H(), json={"body": "nazim"}, timeout=5)
    assert r.status_code == 400 and upstream.calls == []


def test_reset_valid_proxies_body_only(hosted, upstream):
    r = httpx.post(hosted + "/api/reset", headers=H(), json={"body": "cai", "confirm": "cai"}, timeout=5)
    assert r.status_code == 200
    assert upstream.calls[0]["path"] == "/api/reset" and upstream.calls[0]["body"] == {"body": "cai"}


# ------------------------------------------------------------------ apply (token switch)
def test_apply_armed_confirm_required_and_protected_refused(hosted, upstream):
    r = httpx.post(hosted + "/api/apply-armed", headers=H(), json={"session": "cosem-tdu", "kind": "token", "confirm": "nope"}, timeout=5)
    assert r.status_code == 400
    r = httpx.post(hosted + "/api/apply-armed", headers=H(), json={"session": "cai", "kind": "token", "confirm": "cai"}, timeout=5)
    assert r.status_code == 403
    r = httpx.post(hosted + "/api/apply-armed", headers=H(), json={"session": "cosem-tdu", "kind": "bogus", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 400
    assert upstream.calls == []


def test_apply_armed_valid_proxied_and_upstream_503_passes_through(hosted, upstream):
    r = httpx.post(hosted + "/api/apply-armed", headers=H(), json={"session": "cosem-tdu", "kind": "token", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 200
    assert upstream.calls[-1]["body"] == {"session": "cosem-tdu", "kind": "token", "confirm": "cosem-tdu"}
    # the Mini's CONSOLE_R4_ENABLED gate is the executing gate: its 503 reaches the phone as 503
    upstream.reply = (503, {"error": "R4 armed apply is DISABLED"})
    r = httpx.post(hosted + "/api/apply-armed", headers=H(), json={"session": "cosem-tdu", "kind": "token", "confirm": "cosem-tdu"}, timeout=5)
    assert r.status_code == 503


def test_apply_dry_run_needs_no_confirm_and_is_proxied(hosted, upstream):
    r = httpx.post(hosted + "/api/apply-dry-run", headers=H(), json={"session": "cosem-tdu", "kind": "token"}, timeout=5)
    assert r.status_code == 200
    assert upstream.calls[-1]["path"] == "/api/apply-dry-run" and "confirm" not in upstream.calls[-1]["body"]


def test_token_truth_proxied_with_every_fingerprint_stripped(hosted, upstream):
    upstream.reply = (200, {"rows": [{"session": "cosem-tdu", "fp": "68142948c003", "auth_fp": "68142948c003",
                                      "token_settable": True, "token_pointer_name": "musa"}],
                            "registry": {"tokens": [{"name": "musa", "fp": "68142948c003", "available": True}], "models": []}})
    r = httpx.get(hosted + "/api/token-truth", headers=H(), timeout=5)
    assert r.status_code == 200
    assert "68142948c003" not in r.text
    assert r.json()["rows"][0]["token_pointer_name"] == "musa"
    assert r.json()["registry"]["tokens"][0] == {"name": "musa", "available": True}
    assert httpx.get(hosted + "/api/token-truth", timeout=5).status_code == 401


# ------------------------------------------------------------------ assign / ask-close (local vetted scripts)
def test_assign_bad_agent_or_empty_ask_400_no_subprocess(hosted):
    with patch("subprocess.run") as run:
        assert httpx.post(hosted + "/api/assign", headers=H(), json={"agent": "x; rm", "ask": "y"}, timeout=5).status_code == 400
        assert httpx.post(hosted + "/api/assign", headers=H(), json={"agent": "cc-cosem-tdu", "ask": ""}, timeout=5).status_code == 400
    run.assert_not_called()


def test_assign_valid_runs_console_assign_py_like_the_mini(hosted):
    ok = MagicMock(returncode=0, stdout="assigned agent_messages #4242 to cc-cosem-tdu\n", stderr="")
    with patch("subprocess.run", return_value=ok) as run:
        r = httpx.post(hosted + "/api/assign", headers=H(), json={"agent": "cc-cosem-tdu", "ask": "ship it", "priority": "P1"}, timeout=5)
    assert r.status_code == 200 and r.json() == {"ok": True, "agent": "cc-cosem-tdu", "id": 4242}
    argv = run.call_args.args[0]
    assert argv[1].endswith("scripts/console_assign.py") and argv[2:] == ["cc-cosem-tdu", "ship it", "--priority", "P1"]


def test_assign_unknown_agent_exit2_maps_to_400(hosted):
    bad = MagicMock(returncode=2, stdout="", stderr="unknown agent\n")
    with patch("subprocess.run", return_value=bad):
        r = httpx.post(hosted + "/api/assign", headers=H(), json={"agent": "nope", "ask": "x"}, timeout=5)
    assert r.status_code == 400 and r.json()["ok"] is False


def test_assign_without_writable_dsn_503_no_subprocess(hosted, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    with patch("subprocess.run") as run:
        r = httpx.post(hosted + "/api/assign", headers=H(), json={"agent": "cc-cosem-tdu", "ask": "x"}, timeout=5)
    assert r.status_code == 503
    run.assert_not_called()


def test_ask_close_validates_then_runs_asks_close_py(hosted):
    with patch("subprocess.run") as run:
        assert httpx.post(hosted + "/api/ask-close", headers=H(), json={"id": "7", "action": "confirm"}, timeout=5).status_code == 400
        assert httpx.post(hosted + "/api/ask-close", headers=H(), json={"id": 7, "action": "nuke"}, timeout=5).status_code == 400
        assert httpx.post(hosted + "/api/ask-close", headers=H(), json={"id": True, "action": "drop"}, timeout=5).status_code == 400
    run.assert_not_called()
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=ok) as run:
        r = httpx.post(hosted + "/api/ask-close", headers=H(), json={"id": 7, "action": "drop"}, timeout=5)
    assert r.status_code == 200 and r.json()["ok"] is True
    argv = run.call_args.args[0]
    assert argv[1].endswith("scripts/asks_close.py") and argv[2:] == ["7", "drop"]
