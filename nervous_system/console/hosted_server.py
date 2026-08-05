"""Hosted (public, VPS) fleet-console HTTP wrapper — bearer-gated, read-only.

A VISUAL CLONE of the Mac-Mini fleet console: it serves the Mini's OWN static
assets (fleet.html / fleet.js / app.js / sw.js / manifest / icons) verbatim, so
the public console looks + feels identical, and feeds them from /api/fleet with a
payload in the SAME SHAPE fleet.js reads — but produced DB-ONLY (no tmux
pane-captures, impossible/disallowed on the VPS) and SCRUBBED for the public URL
(hosted_view.build_cloned_payload → scrub_field on every free-text value).

This wrapper adds ONLY the transport + the interim-auth guardrails cai approved
(CAI-RESP-720):
  * AUTH: a strong bearer token (CONSOLE_BEARER_TOKEN), checked in the
    Authorization HEADER (never the URL), constant-time compared. /api/fleet (and
    the /api/lanes peek) are bearer-gated; the shell + static assets carry NO data
    (fleet.js prompts for / stores the token and sends it as the header), so an
    unauth visitor sees an empty shell only.
  * READ-ONLY: mutating endpoints (/api/reset, /api/backlog) are refused (403) —
    the public console can never drive a reset or reorder a backlog.
  * KILL-SWITCH: CONSOLE_KILL=1 -> 503 everything (instant disable).
  * NOINDEX: X-Robots-Tag on every response.
  * NO PANES: hosted_view is DB-only (no tmux pane-captures); the peek endpoint
    returns a static "not available on the public console" note (no leak).

Run: CONSOLE_BEARER_TOKEN=... CONSOLE_DB_URL=... python -m nervous_system.console.hosted_server
Refuses to start without a >=24-char token (fail-closed).
"""
from __future__ import annotations

import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg

from . import hosted_view

_TOKEN = os.environ.get("CONSOLE_BEARER_TOKEN", "")
_PORT = int(os.environ.get("HOSTED_CONSOLE_PORT", "8788"))

# The Mini console's static bundle, served verbatim so the hosted console is a
# pixel clone. Resolved relative to this file so it works wherever the VPS unpacks it.
_STATIC_DIR = (Path(__file__).parent / "static").resolve()

_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


def _killed() -> bool:
    return os.environ.get("CONSOLE_KILL") == "1"


def _shell_version() -> str:
    """The build id the served bundle bakes in (sw.js `const VERSION = "fc-vNN"`),
    so /api/version reports the SAME version the static assets carry and fleet.js's
    version gate never force-resets onto a phantom-newer server. Empty if unknown
    (fleet.js then just shows the device build — no reset)."""
    try:
        txt = (_STATIC_DIR / "sw.js").read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', txt)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


class _Handler(BaseHTTPRequestHandler):
    server_version = "wfleet/2"
    protocol_version = "HTTP/1.1"

    # --- security headers on every response ---
    def _sec(self) -> None:
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _authed(self) -> bool:
        h = self.headers.get("Authorization", "")
        if not (h.startswith("Bearer ") and _TOKEN):
            return False
        return hmac.compare_digest(h[7:], _TOKEN)

    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8",
              extra=None, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self._sec()
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload, extra=None) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8", extra=extra)

    # --- static serving (shell + assets), NO auth (they carry no data) ---
    def _serve_static(self, rel: str) -> bool:
        """Serve _STATIC_DIR/rel if it's a real file inside the static dir.
        Returns True if handled (even as 404). Path-traversal safe: the resolved
        path MUST live under _STATIC_DIR."""
        try:
            target = (_STATIC_DIR / rel).resolve()
        except Exception:  # noqa: BLE001
            self._send(404, b"not found")
            return True
        if _STATIC_DIR not in target.parents and target != _STATIC_DIR:
            self._send(403, b"forbidden")
            return True
        if not target.is_file():
            self._send(404, b"not found")
            return True
        ctype = _CTYPES.get(target.suffix.lower(), "application/octet-stream")
        # sw.js must never be cached by the browser (its own update flow depends
        # on always re-fetching it); everything else stays no-store here too — the
        # SW owns client-side caching, the origin does not.
        self._send(200, target.read_bytes(), ctype)
        return True

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        if _killed():
            self._send(503, b"console temporarily disabled")
            return
        path = self.path.split("?", 1)[0]

        if path == "/healthz":
            self._send(200, b"ok")
            return

        # ---- shell + PWA plumbing (served from the Mini bundle) ----
        if path == "/":
            self._serve_static("fleet.html")
            return
        if path in ("/sw.js", "/manifest.json"):
            self._serve_static(path.lstrip("/"))
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return

        # ---- open, non-sensitive: version gate (fleet.js reads it pre-auth) ----
        if path == "/api/version":
            self._json(200, {"version": _shell_version(), "sha": ""})
            return

        # ---- bearer-gated data ----
        if path == "/api/fleet":
            if not self._authed():
                self._json(401, {"error": "unauthorized"}, extra={"WWW-Authenticate": "Bearer"})
                return
            try:
                with psycopg.connect(hosted_view._dsn(), connect_timeout=15) as conn:
                    payload = hosted_view.build_cloned_payload(conn)
                self._json(200, payload)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": str(e)[:200]})
            return

        # ---- live-pane peek: not available DB-only; graceful (no leak) ----
        if path.startswith("/api/lanes/") and path.endswith("/pane"):
            if not self._authed():
                self._json(401, {"error": "unauthorized"}, extra={"WWW-Authenticate": "Bearer"})
                return
            self._json(200, {"text": "Live terminal peek is not available on the "
                                     "public read-only console."})
            return

        self._send(404, b"not found")

    def do_POST(self):  # noqa: N802
        if _killed():
            self._send(503, b"console temporarily disabled")
            return
        path = self.path.split("?", 1)[0]
        # Drain the body so the client doesn't see a broken pipe.
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 0:
                self.rfile.read(n)
        except Exception:  # noqa: BLE001
            pass
        # The public console is strictly read-only: reset / backlog / switch-token
        # mutations are refused here (they exist only on the Mini console's
        # writable env). switch-token (re-token a lane onto a different Claude
        # account) is Mini-only + strongly-authed — never drivable from the public
        # URL.
        if path in ("/api/reset", "/api/backlog", "/api/switch-token"):
            self._json(403, {"ok": False, "error": "read-only console"})
            return
        self._send(404, b"not found")

    def log_message(self, *a):  # quiet — no request logging (privacy)
        return


def run() -> None:
    if not _TOKEN or len(_TOKEN) < 24:
        raise SystemExit("refusing to start: CONSOLE_BEARER_TOKEN missing or < 24 chars (fail-closed)")
    httpd = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)
    print(f"hosted fleet console listening on 0.0.0.0:{_PORT} "
          f"(clone UI, bearer-gated, noindex, kill={_killed()}, version={_shell_version() or '?'})")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
