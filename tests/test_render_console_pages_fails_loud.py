"""render_console_pages.sh must refuse to bake a non-real API body (cc-quality #87225).

The bug these tests pin: `curl -s` exits 0 on 401/404/5xx, so the render harness
baked whatever came back, painted a benign "NO DATA" panel, and deploy_console.sh
Gate 3 still PASSED — fc-v94's resources.png looked broken while the live page was
fine, and a genuinely broken console could ship behind a green render.

These tests run the SHIPPED script (not a re-implementation of its logic) against
a local HTTP server, with the console URL / orchestration dir / Chrome path
overridden by env so nothing touches the live console, the live checkout, or a
real browser. A page that fails the fetch gate must never reach Playwright.
"""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "render_console_pages.sh"

# The smallest payload that satisfies every endpoint's page contract.
GOOD = {
    "/api/fleet": {"pulse": {"lanes": 1}, "lanes": [{"agent_id": "cc-x"}]},
    "/api/token-truth": {"rows": [{"session": "cc-x"}], "summary": {"total": 1}},
    # "zero open asks" is a REAL state: empty is allowed when counts agrees.
    "/api/asks-board": {"open": [], "answered": [], "counts": {"open": 0}},
    "/api/status-board": {"items": [], "counts": {"total": 0}},
    "/api/build-status": {"programmes": [{"name": "p"}], "delivery_times": []},
    "/api/resources": {"hosts": [{"host": "mini"}]},
}


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler contract
        status, body = self.routes.get(
            self.path, (404, json.dumps({"error": "not found"}).encode()))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def serve():
    """Start a fake console on a random loopback port; returns its base URL."""
    servers = []

    def _start(routes: dict) -> str:
        handler = type("_H", (_Handler,), {"routes": routes})
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    yield _start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def orch(tmp_path):
    """A throwaway orchestration dir: a readable sw.js and an EMPTY .env so no
    real secret or pointer leaks into the run."""
    root = tmp_path / "orch"
    static = root / "nervous_system" / "console" / "static"
    static.mkdir(parents=True)
    (static / "sw.js").write_text('const VERSION = "fc-v94";\n')
    (root / ".env").write_text("")
    return root


@pytest.fixture
def chrome(tmp_path):
    p = tmp_path / "fake-chrome"
    p.write_text("#!/bin/sh\nexit 1\n")
    p.chmod(0o755)
    return p


def _run(tmp_path, orch, chrome, url, out):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": str(home),
        "ORCH_DIR": str(orch),
        "CHROME_BIN": str(chrome),
        "CONSOLE_URL": url,
    }
    return subprocess.run(["bash", str(SCRIPT), str(out)], env=env,
                          capture_output=True, text=True, timeout=120)


def test_401_body_fails_the_render_gate(tmp_path, serve, orch, chrome):
    """THE regression: curl exits 0 on a 401, the old script baked it and passed."""
    url = serve({p: (401, b'{"error":"unauthorized"}') for p in GOOD})
    out = tmp_path / "render"
    r = _run(tmp_path, orch, chrome, url, out)

    assert r.returncode != 0, f"a 401 body must fail the gate:\n{r.stdout}\n{r.stderr}"
    assert "HTTP 401" in r.stderr
    assert "rendered" not in r.stdout, "no page may render from an error body"
    assert not list(out.glob("*.png"))


def test_empty_object_body_fails(tmp_path, serve, orch, chrome):
    """The old `|| echo '{}'` fallback shape must not pass either."""
    url = serve({p: (200, b"{}") for p in GOOD})
    out = tmp_path / "render"
    r = _run(tmp_path, orch, chrome, url, out)

    assert r.returncode != 0
    assert "missing top-level key" in r.stderr
    assert not list(out.glob("*.png"))


def test_200_error_json_fails(tmp_path, serve, orch, chrome):
    """A 200 carrying an error envelope (proxy/middleware) is still not data."""
    url = serve({p: (200, b'{"error":"unauthorized"}') for p in GOOD})
    out = tmp_path / "render"
    r = _run(tmp_path, orch, chrome, url, out)

    assert r.returncode != 0
    assert "missing top-level key" in r.stderr


def test_empty_hosts_fails_even_on_200(tmp_path, serve, orch, chrome):
    """fc-v94's exact shape: resources answered 200 with an empty hosts list and
    the gate rendered a NO-DATA page instead of refusing."""
    routes = {p: (200, json.dumps(v).encode()) for p, v in GOOD.items()}
    routes["/api/resources"] = (200, b'{"hosts": []}')
    url = serve(routes)
    out = tmp_path / "render"
    r = _run(tmp_path, orch, chrome, url, out)

    assert r.returncode != 0
    assert "/api/resources" in r.stderr
    assert "empty" in r.stderr
    assert not list(out.glob("*.png"))


def test_inconsistent_counts_fails(tmp_path, serve, orch, chrome):
    """A truncated asks board (counts says 3, rows say 1) is not a real board."""
    routes = {p: (200, json.dumps(v).encode()) for p, v in GOOD.items()}
    routes["/api/asks-board"] = (200, b'{"open": [], "answered": [], "counts": {"open": 3}}')
    url = serve(routes)
    out = tmp_path / "render"
    r = _run(tmp_path, orch, chrome, url, out)

    assert r.returncode != 0
    assert "disagrees" in r.stderr


def test_good_payloads_pass_the_fetch_gate(tmp_path, serve, orch, chrome):
    """Positive control: real payloads get PAST the fetch gate. (The run still
    ends non-zero here because the fixture has no Playwright venv — that is the
    next stage doing its job, not a validation failure.)"""
    url = serve({p: (200, json.dumps(v).encode()) for p, v in GOOD.items()})
    out = tmp_path / "render"
    r = _run(tmp_path, orch, chrome, url, out)

    for path in GOOD:
        assert f"fetched {path} OK" in r.stdout, f"{path} did not pass validation:\n{r.stderr}"
    assert "render: GET" not in r.stderr
    assert "playwright render failed" in r.stderr  # reached the render stage
