"""Hosted (public, VPS) fleet-console HTTP wrapper — bearer-gated; reads DB-only,
lane ACTIONS via a guarded proxy to the lane-host console (Musa op#20684/20687/20692).

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
  * ACTIONS (op#20684/20687/20692 — the operator authorized fleet-mutating actions
    on the hosted console, bearer-gated + typed-confirm): see the "ACTION LAYER"
    block below. This process holds NO tmux and shells to NO lane script itself;
    every tmux-touching action (reset / lane-boot / lane-down / apply) is
    validated here (target ∈ live roster, singleton-protected, typed confirm ==
    target) and then PROXIED to the console on the lane's OWN host (the Mini's
    app.py), whose handlers re-apply the same guards and shell to the vetted
    scripts. No upstream configured -> 503, never a local shell-out. The DB-only
    actions (/api/assign, /api/ask-close) run the same vetted scripts the Mini
    console runs (console_assign.py / asks_close.py) — script missing or no
    writable DSN -> 503. /api/backlog + /api/switch-token stay refused (403).
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

_PORT = int(os.environ.get("HOSTED_CONSOLE_PORT", "8788"))


def _token() -> str:
    """The public bearer (read at call time so a rotated env is honoured on restart
    and tests can monkeypatch it)."""
    return os.environ.get("CONSOLE_BEARER_TOKEN", "")


_TOKEN = _token()   # start-up fail-closed check in run(); _authed() re-reads.

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
        tok = _token()
        if not (h.startswith("Bearer ") and tok and len(tok) >= 24):
            return False
        return hmac.compare_digest(h[7:], tok)

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

        # ---- token/model ground truth for the sheet controls (op#20692): proxied
        # from the default upstream and STRIPPED of every fingerprint field (cond-2:
        # no auth_fp on the public URL — the pool NICKNAME is all the phone needs).
        if path == "/api/token-truth":
            if not self._authed():
                self._json(401, {"error": "unauthorized"}, extra={"WWW-Authenticate": "Bearer"})
                return
            up = _upstream_default()
            if not up:
                self._json(503, {"error": "no upstream console configured"})
                return
            code, payload = _proxy(up, "GET", "/api/token-truth", None, timeout=60)
            self._json(code, _strip_fps(payload) if isinstance(payload, (dict, list)) else payload)
            return

        # ---- per-project governance registry (op#20702 Stage E, fc-v65): proxied
        # read from the default upstream (the Mini reads it via its vetted module;
        # the hosted process holds no registry grant and does no DB read here).
        if path == "/api/governance":
            if not self._authed():
                self._json(401, {"error": "unauthorized"}, extra={"WWW-Authenticate": "Bearer"})
                return
            up = _upstream_default()
            if not up:
                self._json(503, {"error": "no upstream console configured"})
                return
            q = "?fresh=1" if "fresh=1" in (self.path.split("?", 1) + [""])[1] else ""
            code, payload = _proxy(up, "GET", "/api/governance" + q, None, timeout=60)
            self._json(code, payload)
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
        # Read (bounded) + parse the JSON body; drain it either way so the client
        # never sees a broken pipe.
        raw = b""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 0:
                raw = self.rfile.read(min(n, 65536))
        except Exception:  # noqa: BLE001
            raw = b""
        # Bulk re-token / backlog reorder stay Mini-only (never drivable from the
        # public URL) — refused before auth so they are not even probe-able.
        if path in ("/api/backlog", "/api/switch-token", "/api/switch-group",
                    "/api/switch-all", "/api/set-pointer", "/api/set-group-pointer",
                    "/api/add-token", "/api/apply-queue-cancel"):
            self._json(403, {"ok": False, "error": "not available on the hosted console"})
            return
        if path not in _ACTION_ROUTES:
            self._send(404, b"not found")
            return
        if not self._authed():
            _audit(self.client_address[0], path, "401")
            self._json(401, {"error": "unauthorized"}, extra={"WWW-Authenticate": "Bearer"})
            return
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except Exception:  # noqa: BLE001
            _audit(self.client_address[0], path, "400")
            self._json(400, {"error": "bad request"})
            return
        # CAI-RESP-1434: the operator's ARMED key rides X-Armed-Bearer (the
        # Authorization slot here IS the hosted bearer). Handed ONLY to the two
        # armed routes' handlers, which forward it upstream; never logged.
        armed = (self.headers.get("X-Armed-Bearer") or "").strip() if path in _ARMED_FORWARD_PATHS else ""
        handler = _ACTION_ROUTES[path]
        code, payload = handler(self.client_address[0], body, armed) if path in _ARMED_FORWARD_PATHS \
            else handler(self.client_address[0], body)
        self._json(code, payload)

    def log_message(self, *a):  # quiet — no request logging (privacy)
        return


# ============================================================================
# ACTION LAYER (Musa op#20684 / op#20687 / op#20692) — guarded, fail-closed.
#
# The hosted process owns NO tmux and never shells to a lane script. Two shapes:
#   PROXY  — reset / lane-boot / lane-down / apply-armed / apply-dry-run: validate
#            here (target ∈ live roster from the read-only DB, singleton-protected,
#            typed confirm == target), audit, then forward the SAME JSON to the
#            console on the lane's host (CONSOLE_UPSTREAM_URL / _HOSTS), whose
#            app.py handler re-applies its own guards + shells to the vetted script.
#            No upstream for that host -> 503. NEVER a local subprocess.
#   LOCAL  — assign / ask-close: DB-only, reversible; the same vetted scripts the
#            Mini runs (scripts/console_assign.py, scripts/asks_close.py) with the
#            same validation. Script missing or no DATABASE_URL -> 503.
# ============================================================================
import re as _re          # noqa: E402
import subprocess          # noqa: E402
import urllib.error        # noqa: E402
import urllib.request      # noqa: E402
from datetime import datetime, timezone  # noqa: E402

_SESSION_RE = _re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# CAI-RESP-1434: the Mini's app.py now requires CONSOLE_ARMED_BEARER on these two
# routes even from an allowlisted peer (this proxy included). The phone sends the
# operator's armed key as `X-Armed-Bearer` (its Authorization header is the hosted
# bearer, so that slot is taken); this wrapper forwards it upstream UNCHANGED for
# these two routes ONLY — lane-boot / lane-down / dry-run / assign never see it.
# The upstream's own `Authorization: Bearer <CONSOLE_UPSTREAM_TOKEN>` semantics are
# kept as-is; app.py accepts the armed key from either slot.
# fc-v65 (op#20702 Stage E): /api/governance-set is the third ARMED route — the
# Mini's app.py gates it on the same bearer (+ R4 flag + typed confirm); this
# wrapper only validates shape + confirm, then forwards with the key. One
# enforcement point (the Mini), never a local write from the hosted process.
_ARMED_FORWARD_PATHS = ("/api/reset", "/api/apply-armed", "/api/governance-set")
_AGENT_RE = _re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]
# The three resettable singletons — MIRRORS app.py RESET_ACTIONS exactly (the
# upstream re-checks; this only stops an unknown body from ever leaving here).
_RESET_BODIES = ("nazim", "cai", "hub")
# Bodies that must never be booted / wound down / re-pooled from the phone —
# mirrors app.py _LANE_ACTION_PROTECTED (lane_winddown.SINGLETONS + hub/SRE/qa ids).
_PROTECTED = frozenset({"nazim", "cai", "orch", "orchestrator", "fleet-health",
                        "fleet-console", "quality", "hub", "cc-orchestrator",
                        "cc-fleet-health", "cc-quality", "orch-console"})


def _audit(client: str, action: str, outcome: str) -> None:
    """Attributable append-only audit line (ts, client ip, action:target, outcome).
    Never raises. Path: CONSOLE_HOSTED_AUDIT_LOG (default logs/hosted_console_actions.log)."""
    try:
        p = Path(os.environ.get("CONSOLE_HOSTED_AUDIT_LOG")
                 or (_REPO_ROOT / "logs" / "hosted_console_actions.log"))
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()}\t{client}\t{action}\t{outcome}\n")
    except Exception:  # noqa: BLE001
        pass


# ---- upstream resolution --------------------------------------------------------
def _upstream_hosts() -> dict:
    """agent_status.host -> console base URL. CONSOLE_UPSTREAM_HOSTS="Sheikhs-Mini=
    http://100.83.21.34:8787,gzbai=http://..." plus CONSOLE_UPSTREAM_URL as the
    default entry for CONSOLE_UPSTREAM_HOST (default 'Sheikhs-Mini')."""
    out = {}
    for part in (os.environ.get("CONSOLE_UPSTREAM_HOSTS") or "").split(","):
        if "=" in part:
            h, u = part.split("=", 1)
            if h.strip() and u.strip().startswith("http"):
                out[h.strip()] = u.strip().rstrip("/")
    default = (os.environ.get("CONSOLE_UPSTREAM_URL") or "").strip().rstrip("/")
    if default.startswith("http"):
        out.setdefault(os.environ.get("CONSOLE_UPSTREAM_HOST") or "Sheikhs-Mini", default)
    return out


def _upstream_default() -> str:
    default = (os.environ.get("CONSOLE_UPSTREAM_URL") or "").strip().rstrip("/")
    if default.startswith("http"):
        return default
    hosts = _upstream_hosts()
    return next(iter(hosts.values()), "") if hosts else ""


def _upstream_token() -> str:
    return os.environ.get("CONSOLE_UPSTREAM_TOKEN") or os.environ.get("CONSOLE_TOKEN") or ""


def _proxy(base: str, method: str, path: str, body, timeout: int = 200, armed: str = ""):
    """Forward one request to an upstream console. Returns (status, json|{error}).
    `armed` (CAI-RESP-1434) = the operator's armed key, forwarded as X-Armed-Bearer;
    only the reset / apply-armed handlers ever pass it."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Wingmen-Hosted-Actor", "hosted-console")
    tok = _upstream_token()
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    if armed and path in _ARMED_FORWARD_PATHS:
        req.add_header("X-Armed-Bearer", armed)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-configured tailnet URL
            return resp.status, _parse_json(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_json(e.read())
    except Exception as e:  # noqa: BLE001
        return 502, {"error": f"lane-host console unreachable: {type(e).__name__}"}


def _parse_json(b: bytes):
    try:
        return json.loads(b or b"{}")
    except Exception:  # noqa: BLE001
        return {"error": (b or b"")[:160].decode("utf-8", "replace")}


def _strip_fps(node):
    """Recursively drop fingerprint keys (fp / auth_fp / *_fp) — cond-2 on the public URL."""
    if isinstance(node, dict):
        return {k: _strip_fps(v) for k, v in node.items()
                if not (k == "fp" or k.endswith("_fp"))}
    if isinstance(node, list):
        return [_strip_fps(x) for x in node]
    return node


# ---- read-only DB lookups (console_readonly role) ----------------------------------
def _db_lane_roster() -> set:
    """Worker-lane names (fleet_lanes.lane, launcher = launch_dangerous_cc.sh)."""
    with psycopg.connect(hosted_view._dsn(), connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute("SELECT lane FROM fleet_lanes WHERE launcher = %s AND lane IS NOT NULL",
                    ("launch_dangerous_cc.sh",))
        return {r[0] for r in cur.fetchall()}


def _db_lane_host(session: str):
    """The host the session last heart-beat from (agent_status), or None."""
    with psycopg.connect(hosted_view._dsn(), connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute("SELECT host FROM agent_status WHERE tmux_session = %s "
                    "ORDER BY last_heartbeat DESC NULLS LAST LIMIT 1", (session,))
        row = cur.fetchone()
        return row[0] if row else None


def _resolve_upstream_for(session: str):
    """(base_url, host) for the console that owns this session, or (None, reason)."""
    try:
        host = _db_lane_host(session)
    except Exception as e:  # noqa: BLE001
        return None, f"could not resolve the lane's host: {type(e).__name__}"
    if not host:
        return None, "lane host unknown (no heartbeat on record) — act from the host console"
    up = _upstream_hosts().get(host)
    if not up:
        return None, f"lane lives on '{host}' — no action console configured for that host"
    return up, host


# ---- the actions -----------------------------------------------------------------------
def _act_reset(client: str, body: dict, armed: str = ""):
    target = str(body.get("body") or "").strip()
    confirm = str(body.get("confirm") or "").strip()
    if target not in _RESET_BODIES:
        _audit(client, f"/api/reset:{target}", "400")
        return 400, {"error": "unknown body", "allowed": list(_RESET_BODIES)}
    if confirm != target:
        _audit(client, f"/api/reset:{target}:confirm-miss", "400")
        return 400, {"error": "type the exact body name to confirm"}
    up = _upstream_default()
    if not up:
        _audit(client, f"/api/reset:{target}", "503")
        return 503, {"error": "no upstream console configured — reset not available here"}
    _audit(client, f"/api/reset:{target}", "proxy")
    code, payload = _proxy(up, "POST", "/api/reset", {"body": target}, armed=armed)
    _audit(client, f"/api/reset:{target}", str(code))
    return code, payload


def _act_lane(action: str):
    route = f"/api/lane-{action}"

    def handler(client: str, body: dict):
        session = str(body.get("session") or "").strip()
        confirm = str(body.get("confirm") or "").strip()
        if not _SESSION_RE.match(session):
            _audit(client, route, "400")
            return 400, {"error": "bad session"}
        if session in _PROTECTED or session.startswith("cc-"):
            _audit(client, f"{route}:{session}:protected", "403")
            return 403, {"error": f"'{session}' is a protected body, not a worker lane"}
        try:
            roster = _db_lane_roster()
        except Exception as e:  # noqa: BLE001
            _audit(client, f"{route}:{session}:roster-unavailable", "503")
            return 503, {"error": f"lane roster unavailable — refusing (fail-closed): {type(e).__name__}"}
        if session not in roster:
            _audit(client, f"{route}:{session}:unknown", "400")
            return 400, {"error": "unknown lane", "session": session}
        if confirm != session:
            _audit(client, f"{route}:{session}:confirm-miss", "400")
            return 400, {"error": "type the exact lane name to confirm"}
        up, host = _resolve_upstream_for(session)
        if not up:
            _audit(client, f"{route}:{session}:no-upstream", "503")
            return 503, {"error": host, "session": session}
        _audit(client, f"{route}:{session}@{host}", "proxy")
        code, payload = _proxy(up, "POST", route, {"session": session, "confirm": confirm})
        _audit(client, f"{route}:{session}@{host}", str(code))
        return code, payload
    return handler


def _act_apply(armed: bool):
    route = "/api/apply-armed" if armed else "/api/apply-dry-run"

    def handler(client: str, body: dict, armed_key: str = ""):
        # `armed` (closure) = this is the ARMED route; `armed_key` = the operator's
        # X-Armed-Bearer to forward (CAI-RESP-1434) — only meaningful when armed.
        session = str(body.get("session") or "").strip()
        kind = str(body.get("kind") or "").strip().lower()
        confirm = str(body.get("confirm") or "").strip()
        if not _SESSION_RE.match(session) or kind not in ("token", "model"):
            _audit(client, route, "400")
            return 400, {"error": "bad session/kind"}
        if armed and session in _PROTECTED and session != "nazim":
            # the only pointer-settable singleton is nazim (switch_singleton_token.sh);
            # every other singleton / the hub is refused here as on the Mini.
            _audit(client, f"{route}:{session}:protected", "403")
            return 403, {"error": f"'{session}' cannot be re-pooled from the hosted console"}
        if armed and confirm != session:
            _audit(client, f"{route}:{session}:confirm-miss", "400")
            return 400, {"error": "type the exact body name to confirm"}
        up, host = _resolve_upstream_for(session)
        if not up:
            _audit(client, f"{route}:{session}:no-upstream", "503")
            return 503, {"error": host, "session": session}
        fwd = {"session": session, "kind": kind}
        if armed:
            fwd["confirm"] = confirm
        _audit(client, f"{route}:{kind}:{session}@{host}", "proxy")
        code, payload = _proxy(up, "POST", route, fwd, timeout=200 if armed else 45,
                               armed=(armed_key if armed else ""))
        _audit(client, f"{route}:{kind}:{session}@{host}", str(code))
        return code, payload
    return handler


def _venv_python() -> str:
    v = _REPO_ROOT / ".venv" / "bin" / "python3"
    return str(v) if v.is_file() else "python3"


def _local_script(client: str, route: str, script: str, argv: list, timeout: int = 30):
    """Run one vetted DB-writing script (the Mini's own shell-out rail). Fail-closed
    when the script or a writable DSN is absent."""
    path = _REPO_ROOT / "scripts" / script
    if not path.is_file():
        _audit(client, route, "503")
        return 503, {"ok": False, "error": f"{script} not present on this host"}
    if not (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")):
        _audit(client, route, "503")
        return 503, {"ok": False, "error": "no writable DSN on this host"}
    _audit(client, route, "run")
    try:
        r = subprocess.run([_venv_python(), str(path)] + argv,
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        _audit(client, route, "500")
        return 500, {"ok": False, "error": f"{script} failed: {type(e).__name__}"}
    return r


def _act_assign(client: str, body: dict):
    agent = str(body.get("agent") or "").strip()
    ask = str(body.get("ask") or "").strip()
    priority = str(body.get("priority") or "P2").strip().upper()
    if not agent or not _AGENT_RE.match(agent):
        _audit(client, "/api/assign", "400")
        return 400, {"error": "bad agent id"}
    if not ask:
        _audit(client, f"/api/assign:{agent}", "400")
        return 400, {"error": "empty ask"}
    if priority not in ("P0", "P1", "P2"):
        priority = "P2"
    ask = ask[:1000]
    res = _local_script(client, f"/api/assign:{agent}:{priority}", "console_assign.py",
                        [agent, ask, "--priority", priority])
    if isinstance(res, tuple):
        return res
    ok = res.returncode == 0
    _audit(client, f"/api/assign:{agent}", "200" if ok else "500")
    if not ok:
        code = 400 if res.returncode == 2 else 500
        return code, {"ok": False, "agent": agent,
                      "error": (res.stderr or "").strip()[-160:] or "assign failed"}
    m = _re.search(r"assigned agent_messages #(\d+)", res.stdout or "")
    return 200, {"ok": True, "agent": agent, "id": int(m.group(1)) if m else None}


def _act_ask_close(client: str, body: dict):
    item_id = body.get("id")
    action = str(body.get("action") or "confirm").strip().lower()
    if not isinstance(item_id, int) or isinstance(item_id, bool) or action not in ("confirm", "drop"):
        _audit(client, f"/api/ask-close:{action}", "400")
        return 400, {"error": "bad id/action"}
    res = _local_script(client, f"/api/ask-close:{action}:{item_id}", "asks_close.py",
                        [str(item_id), action])
    if isinstance(res, tuple):
        return res
    ok = res.returncode == 0
    _audit(client, f"/api/ask-close:{action}:{item_id}", "200" if ok else "500")
    return (200 if ok else 500), {"ok": ok, "id": item_id, "action": action,
                                  "error": None if ok else (res.stderr or "").strip()[-160:]}


_GOV_PROJECT_RE = _re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_GOV_FIELDS = ("cai_enabled", "money_clearance_enabled", "operators", "channels")


def _act_governance_set(client: str, body: dict, armed_key: str = ""):
    """PROXY shape: validate the body's SHAPE + typed confirm here (so a malformed
    request never leaves the phone), then forward the same JSON + the operator's
    armed key to the DEFAULT upstream (the Mini), whose app.py applies the real
    gates (bearer >= 24, R4 flag, confirm, value validation, audited write)."""
    project = str(body.get("project") or "").strip()
    field = str(body.get("field") or "").strip()
    confirm = str(body.get("confirm") or "").strip()
    reason = str(body.get("reason") or "").strip()
    if not _GOV_PROJECT_RE.match(project) or field not in _GOV_FIELDS:
        _audit(client, "/api/governance-set", "400")
        return 400, {"error": "bad project/field"}
    if confirm != project:
        _audit(client, f"/api/governance-set:{project}:confirm-miss", "400")
        return 400, {"error": "type the exact project name to confirm"}
    if not reason:
        _audit(client, f"/api/governance-set:{project}:{field}", "400")
        return 400, {"error": "a reason is required"}
    up = _upstream_default()
    if not up:
        _audit(client, f"/api/governance-set:{project}:no-upstream", "503")
        return 503, {"error": "no upstream console configured"}
    fwd = {"project": project, "field": field, "value": body.get("value"), "confirm": confirm,
           "reason": reason[:500], "money_ack": str(body.get("money_ack") or "")[:64]}
    _audit(client, f"/api/governance-set:{project}:{field}", "proxy")
    code, payload = _proxy(up, "POST", "/api/governance-set", fwd, timeout=90, armed=armed_key)
    _audit(client, f"/api/governance-set:{project}:{field}", str(code))
    return code, payload


_ACTION_ROUTES = {
    "/api/reset": _act_reset,
    "/api/governance-set": _act_governance_set,
    "/api/lane-boot": _act_lane("boot"),
    "/api/lane-down": _act_lane("down"),
    "/api/apply-armed": _act_apply(armed=True),
    "/api/apply-dry-run": _act_apply(armed=False),
    "/api/assign": _act_assign,
    "/api/ask-close": _act_ask_close,
}


def run() -> None:
    if not _TOKEN or len(_TOKEN) < 24:
        raise SystemExit("refusing to start: CONSOLE_BEARER_TOKEN missing or < 24 chars (fail-closed)")
    httpd = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)
    print(f"hosted fleet console listening on 0.0.0.0:{_PORT} "
          f"(clone UI, bearer-gated, noindex, kill={_killed()}, version={_shell_version() or '?'}, "
          f"upstreams={sorted(_upstream_hosts()) or 'NONE (lane actions 503)'})")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
