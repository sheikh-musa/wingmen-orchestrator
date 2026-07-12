"""Fleet Console v1 HTTP app (CAI-RESP-264).

A dependency-light, stdlib-only HTTP server (http.server, ThreadingHTTPServer)
serving:
  GET /                 -> static SPA (index.html)
  GET /static/<file>    -> SPA assets (app.js)
  GET /healthz          -> liveness (open, no auth)
  GET /api/messages     -> recent bus rows (auth, PII-redacted)
  GET /api/lanes        -> agents ⋈ agent_status ⋈ fleet_lanes (auth)
  GET /api/stream       -> SSE live feed of new bus rows (auth)

Why stdlib, not FastAPI/uvicorn: neither is in the venv, and the hard constraint
is "no new heavyweight deps if avoidable". http.server + psycopg (already
present) covers the entire read-only surface and keeps the module trivially
VPS-portable.

Process isolation (condition 5): this module is launched as its OWN process
(`python -m nervous_system.console`), never inside wingmen_orch. A background
asyncio loop owns the Broadcaster + feeder for SSE; the threaded HTTP server
bridges to it via run_coroutine_threadsafe.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from nervous_system.console import auth, db, docs, media, panes, pii
from nervous_system.console.feed import Broadcaster, feeder

logger = logging.getLogger("wingmen.console.app")

_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8787
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}

class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that doesn't dump a full traceback for benign client
    disconnects. A phone closing an idle keep-alive socket, or an EventSource
    the browser aborts, surfaces as ConnectionResetError [Errno 54] /
    BrokenPipeError raised deep in socketserver — the stock handle_error prints
    a ~15-line traceback per event, which had grown to be ~100% of console.log
    and buried every real error. These are expected and handled; log one terse
    line at DEBUG and move on. Any OTHER exception still gets the full trace."""

    def handle_error(self, request, client_address):  # noqa: D102
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            logger.debug("client %s disconnected: %s", client_address, exc)
            return
        super().handle_error(request, client_address)


# --- shared SSE infrastructure (one event loop per server) --------------------


class _FeedLoop:
    """Owns an asyncio loop in a background thread for the SSE broadcaster."""

    def __init__(self, fetch_since=None) -> None:
        self.loop = asyncio.new_event_loop()
        self.broadcaster = Broadcaster()
        self._fetch_since = fetch_since or _fetch_since
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = None
        self._ready = threading.Event()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._stop = asyncio.Event()
        self.loop.create_task(
            feeder(self.broadcaster, self._fetch_since, stop_event=self._stop)
        )
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        """Stop the feeder + event loop (lets stale loops die between tests)."""
        try:
            if self._stop is not None:
                self.loop.call_soon_threadsafe(self._stop.set)

            def _cancel_and_stop():
                for task in asyncio.all_tasks(self.loop):
                    task.cancel()
                self.loop.stop()

            self.loop.call_soon_threadsafe(_cancel_and_stop)
        except Exception:
            pass

    def subscribe(self):
        fut = asyncio.run_coroutine_threadsafe(self._async_subscribe(), self.loop)
        return fut.result(timeout=5)

    async def _async_subscribe(self):
        return self.broadcaster.subscribe()

    def get_next(self, q, timeout: float):
        """Block (in the HTTP thread) for the next event, or None on timeout."""
        async def _get():
            try:
                return await asyncio.wait_for(q.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None

        fut = asyncio.run_coroutine_threadsafe(_get(), self.loop)
        try:
            return fut.result(timeout=timeout + 2)
        except Exception:
            return None

    def unsubscribe(self, q) -> None:
        asyncio.run_coroutine_threadsafe(
            self._async_unsubscribe(q), self.loop
        )

    async def _async_unsubscribe(self, q) -> None:
        self.broadcaster.unsubscribe(q)


def _enrich_lanes_live(rows):
    """Fold a LIVE tmux summary into each lane row (shared by /api/lanes and
    /api/fleet) so a card shows what the lane is ACTUALLY doing now, not the
    stale boot-string current_task. One read-only capture_pane per lane."""
    for r in rows:
        sess = r.get("tmux_session") or r.get("lane")
        # the orchestrator doesn't self-register a tmux session; target its
        # known 'orch' session so the operator can peek the hub (operator 2637).
        if not r.get("tmux_session") and str(r.get("agent_id", "")).startswith("cc-orchestrator"):
            sess = "orch"
            r["tmux_session"] = "orch"
        txt = panes.capture_pane(sess) if sess else None
        if not txt:
            r["live"] = {"running": False}
            continue
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        spinner = next((ln for ln in reversed(lines)
                        if ln[:1] in "✻✳✶✷✸✹✺✽❋⚹"), None)
        active = bool(spinner and ("token" in spinner.lower()
                      or "↑" in spinner or "↓" in spinner or "…" in spinner))
        working = active or any("esc to interrupt" in ln.lower() for ln in lines)
        activity = spinner or (lines[-1] if lines else "")
        r["live"] = {"running": True,
                     "state": "working" if working else "idle",
                     "activity": activity[:140]}
    return rows


def _lane_bucket(l):
    """working / idle / offline / flagged classification for a lane row, from
    live tmux state + heartbeat + desired_state (the redesign's exception-first
    + working-first sort keys). 'flagged' = desired up but heartbeat dead/stale."""
    hb = l.get("heartbeat_age_s")
    desired_up = str(l.get("desired_state") or "").lower() == "up"
    live = l.get("live") or {}
    dead = hb is None or hb >= 900
    stale = hb is not None and 120 <= hb < 900
    flagged = desired_up and (dead or stale)
    if live.get("running") and live.get("state") == "working":
        state = "working"
    elif live.get("running"):
        state = "idle"
    elif dead:
        state = "offline"
    else:
        state = "idle"
    return state, flagged


def _fleet_payload():
    """The single live-derived aggregate behind /api/fleet (redesign #7576):
    pulse counts + needs-you hero + exception-first flagged lanes + working-first
    lanes + deploys, all from real tables. One round-trip for the phone."""
    lanes = _enrich_lanes_live(db.fetch_lanes())
    deploys = db.fetch_deploys()
    needs = db.fetch_needs_you()
    coordinators = db.fetch_coordinators()

    counts = {"working": 0, "idle": 0, "offline": 0, "flagged": 0}
    for l in lanes:
        state, flagged = _lane_bucket(l)
        l["bucket"] = state
        l["flagged"] = flagged
        counts[state] = counts.get(state, 0) + 1
        if flagged:
            counts["flagged"] += 1

    # working first, then idle, then offline; flagged floats up within its group.
    order = {"working": 0, "idle": 1, "offline": 2}
    lanes.sort(key=lambda l: (0 if l["flagged"] else 1, order.get(l["bucket"], 3)))

    return {
        "pulse": {**counts, "needs_you": len(needs)},
        "needs_you": _jsonable(needs),
        "coordinators": _jsonable(coordinators),
        "lanes": _jsonable(lanes),
        "deploys": _jsonable(deploys),
    }


def _fetch_since(last_id):
    """Feeder callback: rows with id > last_id (or baseline when None)."""
    if last_id is None:
        return db.fetch_messages(limit=1)
    sql = (
        "SELECT id, thread_id, from_agent, to_agent, message_type, subject, "
        "body, priority, requires_response, sub_tag, created_at "
        "FROM agent_messages WHERE id > %s ORDER BY id ASC LIMIT %s"
    )
    return db._query(sql, [last_id, db.MAX_LIMIT])


# --- request handler ----------------------------------------------------------


def _make_handler(feedloop: "_FeedLoop"):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FleetConsole/1.0"
        protocol_version = "HTTP/1.1"

        # Silence default stderr logging; we audit explicitly.
        def log_message(self, fmt, *args):  # noqa: A003
            return

        # --- helpers ---
        def _client(self) -> str:
            # Real TCP peer ONLY. X-Forwarded-For is client-supplied and
            # trivially spoofed — trusting it (for auth OR the audit trail)
            # would make IP-allowlisting worse than the password it replaces,
            # and would launder a spoofed identity into the log exactly when
            # it matters most (a breakglass/incident review).
            return self.client_address[0] if self.client_address else "-"

        def _json(self, code: int, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            authed, _method = auth.check_access(self._client(), dict(self.headers))
            return authed

        # --- routing ---
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/healthz":
                auth.audit(self._client(), path, "200")
                return self._json(200, {"ok": True})

            if path == "/classic" or path == "/index.html":
                return self._serve_static("index.html", path)

            # Attention-first Fleet view (redesign #7576) — served alongside the
            # classic console during review; flips to "/" once the operator OKs.
            if path == "/" or path == "/fleet" or path == "/fleet/":
                return self._serve_static("fleet.html", path)

            # DOCS section: /docs and any /docs/<repo>/<path> deep link all serve
            # the same SPA shell (open, like /). The shell reads window.location
            # + the stored bearer token and fetches /api/docs* with the header,
            # keeping auth header-only end to end (matches the rest of the SPA).
            if path == "/docs" or path == "/docs/" or path.startswith("/docs/"):
                return self._serve_static("docs.html", path)

            # SCREENSHOTS section: mirrors DOCS — open SPA shell; the page fetches
            # /api/media* with the stored bearer token (header-only).
            if path == "/media" or path == "/media/" or path.startswith("/media/"):
                return self._serve_static("media.html", path)

            if path.startswith("/static/"):
                name = path[len("/static/"):]
                return self._serve_static(name, path)

            # PWA shell: manifest + service worker + its precached assets
            # MUST be reachable pre-auth, same tier as the SPA shell above —
            # a browser can't fetch the manifest or register the SW behind a
            # 401, which would silently break Add-to-Home-Screen/offline
            # while the app still looks fine (orch probe finding, 2026-07-03).
            # Neither is sensitive: static, non-PII, read-only assets.
            if path == "/manifest.json":
                return self._serve_static("manifest.json", path)

            if path == "/sw.js":
                # no-cache: the SW script itself must always be revalidated
                # by the browser's HTTP layer, independent of its own
                # internal cache — serving a stale sw.js is a second, easy
                # way to make "the app never updates" happen.
                return self._serve_static("sw.js", path, cache_control="no-cache")

            # Everything below is auth-gated.
            if not self._authed():
                auth.audit(self._client(), path, "401")
                return self._json(401, {"error": "unauthorized"})

            try:
                if path == "/api/messages":
                    qs = parse_qs(parsed.query)
                    limit = int(qs.get("limit", [db.DEFAULT_LIMIT])[0])
                    thread = qs.get("thread", [None])[0]
                    agent = qs.get("agent", [None])[0]
                    rows = db.fetch_messages(limit=limit, thread=thread, agent=agent)
                    rows = [pii.redact_message_row(r) for r in rows]
                    auth.audit(self._client(), path, "200")
                    return self._json(200, _jsonable(rows))

                if path == "/api/lanes":
                    # Fold a LIVE tmux summary into each lane (shared helper,
                    # also used by /api/fleet) so the card shows what the lane is
                    # ACTUALLY doing now, not the stale boot-string current_task.
                    rows = _enrich_lanes_live(db.fetch_lanes())
                    auth.audit(self._client(), path, "200")
                    return self._json(200, _jsonable(rows))

                if path == "/api/fleet":
                    # Single live-derived aggregate for the attention-first Fleet
                    # view (redesign #7576): pulse + needs-you + lanes + deploys.
                    payload = _fleet_payload()
                    auth.audit(self._client(), path, "200")
                    return self._json(200, payload)

                if path.startswith("/api/lanes/") and path.endswith("/pane"):
                    # Live tmux pane peek (read-only) — operator-requested,
                    # thread f869956c/msg 6156. session is validated against
                    # the REAL live tmux session list inside capture_pane;
                    # an unrecognized name (or anything crafted to look like
                    # one) just 404s, it never reaches a subprocess call.
                    session = unquote(path[len("/api/lanes/"):-len("/pane")])
                    text = panes.capture_pane(session)
                    if text is None:
                        auth.audit(self._client(), path, "404")
                        return self._json(404, {"error": "session not live"})
                    auth.audit(self._client(), path, "200")
                    return self._json(200, {"session": session, "text": text})

                if path == "/api/deploys":
                    rows = db.fetch_deploys()
                    auth.audit(self._client(), path, "200")
                    return self._json(200, _jsonable(rows))

                if path == "/api/queue":
                    rows = db.fetch_queue()
                    auth.audit(self._client(), path, "200")
                    return self._json(200, _jsonable(rows))

                if path == "/api/docs":
                    # Catalog of all fleet docs, grouped by repo/vertical.
                    groups = docs.list_docs()
                    auth.audit(self._client(), path, "200")
                    return self._json(200, _jsonable(groups))

                if path.startswith("/api/docs/"):
                    # /api/docs/<repo>/<rel/path.md> -> one rendered doc.
                    rest = path[len("/api/docs/"):]
                    repo, sep, rel = rest.partition("/")
                    repo = unquote(repo)
                    rel = unquote(rel)
                    if not sep or not repo or not rel:
                        auth.audit(self._client(), path, "404")
                        return self._json(404, {"error": "not found"})
                    doc = docs.read_doc(repo, rel)
                    if doc is None:
                        auth.audit(self._client(), path, "404")
                        return self._json(404, {"error": "not found"})
                    auth.audit(self._client(), path, "200")
                    return self._json(200, _jsonable(doc))

                if path == "/api/media":
                    # Catalog of all screenshots/assets, grouped by project folder.
                    groups = media.list_media()
                    auth.audit(self._client(), path, "200")
                    return self._json(200, _jsonable(groups))

                if path.startswith("/api/media-file/"):
                    # /api/media-file/<project>/<rel/path.png> -> raw image/pdf bytes.
                    rest = path[len("/api/media-file/"):]
                    project, sep, rel = rest.partition("/")
                    project = unquote(project)
                    rel = unquote(rel)
                    if not sep or not project or not rel:
                        auth.audit(self._client(), path, "404")
                        return self._json(404, {"error": "not found"})
                    blob = media.read_media_bytes(project, rel)
                    if blob is None:
                        auth.audit(self._client(), path, "404")
                        return self._json(404, {"error": "not found"})
                    raw, ctype = blob
                    auth.audit(self._client(), path, "200")
                    return self._bytes(200, raw, ctype)

                if path == "/api/stream":
                    return self._serve_sse(parsed)
            except Exception as e:  # read failures are 500, never a write
                logger.warning("api error on %s: %s", path, e)
                auth.audit(self._client(), path, "500")
                return self._json(500, {"error": "internal"})

            auth.audit(self._client(), path, "404")
            return self._json(404, {"error": "not found"})

        def _serve_static(self, name: str, path: str, cache_control: str = None) -> None:
            # Prevent path traversal. is_relative_to (not a string-prefix
            # check) so a sibling like static-x/ can't false-accept just
            # because "static-x" starts with "static" (cc-reviewer-4 advisory
            # — pre-existing, not exploitable today since no such sibling
            # exists, hardened anyway).
            safe = (_STATIC_DIR / name).resolve()
            if not safe.is_relative_to(_STATIC_DIR.resolve()) or not safe.is_file():
                auth.audit(self._client(), path, "404")
                return self._json(404, {"error": "not found"})
            ctype = _CONTENT_TYPES.get(safe.suffix, "text/plain; charset=utf-8")
            if safe.name == "manifest.json":
                ctype = "application/manifest+json"
            data = safe.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(data)
            auth.audit(self._client(), path, "200")

        def _sse_chunk(self, text: str) -> None:
            """Write one HTTP/1.1 chunked-transfer frame (SSE body)."""
            data = text.encode("utf-8")
            self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
            self.wfile.write(data)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        def _serve_sse(self, parsed) -> None:
            auth.audit(self._client(), "/api/stream", "200")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            # Chunked transfer so the threaded HTTP/1.1 server can stream
            # indefinitely without a Content-Length.
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            q = feedloop.subscribe()
            try:
                # Initial comment so EventSource opens immediately.
                self._sse_chunk(": connected\n\n")
                while True:
                    evt = feedloop.get_next(q, timeout=15.0)
                    if evt is None:
                        # Heartbeat keeps the connection (and proxies) alive.
                        self._sse_chunk(": ping\n\n")
                        continue
                    if not evt.get("_resync"):
                        evt = pii.redact_message_row(evt)
                    payload = json.dumps(_jsonable(evt))
                    self._sse_chunk(f"data: {payload}\n\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                feedloop.unsubscribe(q)
                try:
                    self.wfile.write(b"0\r\n\r\n")  # terminating chunk
                    self.wfile.flush()
                except OSError:
                    pass

    return Handler


def _jsonable(obj):
    """Best-effort JSON coercion (datetimes, UUIDs -> str)."""
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def make_server(
    host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT, fetch_since=None
) -> ThreadingHTTPServer:
    feedloop = _FeedLoop(fetch_since=fetch_since)
    feedloop.start()
    handler = _make_handler(feedloop)
    httpd = _QuietThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.feedloop = feedloop  # for lifecycle / shutdown

    # Tear the feed loop down when the server is shut down so stale background
    # loops don't linger (matters for the test suite and clean restarts).
    _orig_shutdown = httpd.shutdown

    def _shutdown():
        _orig_shutdown()
        feedloop.stop()

    httpd.shutdown = _shutdown  # type: ignore[assignment]
    return httpd


def _resolve_host(configured: str) -> str:
    """CONSOLE_HOST='auto' (or 'tailscale') binds this host's OWN tailnet IP,
    resolved at startup — a hardcoded IP in .env crash-loops the console the
    moment the machine re-registers with a new tailnet address (2026-06-19),
    and breaks again on every host migration (Mini→Studio→Linux)."""
    if configured not in ("auto", "tailscale"):
        return configured
    for ts in ("/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
               "/Applications/Tailscale.app/Contents/MacOS/Tailscale", "tailscale"):
        try:
            out = subprocess.run([ts, "ip", "-4"], capture_output=True,
                                 text=True, timeout=5).stdout.strip().splitlines()
            if out and out[0].startswith("100."):
                return out[0]
        except Exception:
            continue
    logger.warning("CONSOLE_HOST=auto but tailscale IP unresolvable — binding 127.0.0.1 (fail-closed)")
    return "127.0.0.1"


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    host = _resolve_host(os.environ.get("CONSOLE_HOST", _DEFAULT_HOST))
    port = int(os.environ.get("CONSOLE_PORT", str(_DEFAULT_PORT)))
    if not os.environ.get("CONSOLE_ALLOWED_IPS"):
        logger.warning(
            "CONSOLE_ALLOWED_IPS is not set — auth fails closed; all /api "
            "requests 401 (an empty allowlist must never mean 'open')."
        )
    if os.environ.get("CONSOLE_BREAKGLASS_TOKEN"):
        logger.info("CONSOLE_BREAKGLASS_TOKEN is configured (dormant recovery path).")
    httpd = make_server(host, port)
    logger.info("Fleet Console listening on http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
