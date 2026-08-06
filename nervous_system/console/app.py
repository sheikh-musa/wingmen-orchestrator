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
import hashlib
import json
import logging
import os
import pathlib
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from nervous_system.console import auth, db, docs, media, panes, pii
from nervous_system.console.feed import Broadcaster, feeder

logger = logging.getLogger("wingmen.console.app")

_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8787
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}

# Operator-triggered context reset from the console — the SAME proven scripts the
# Telegram clear-buttons run (ingest.BUTTON_ACTIONS), exposed as an authenticated
# POST. Each script is fail-safe on its own: BUSY-refuse, composer-preserve, and a
# handoff/checkpoint gate, so a mis-tap can't wipe work in flight. Allowlist only —
# an unknown body id fails closed (400) and never reaches subprocess.
RESET_ACTIONS = {
    "nazim": ("scripts/reset_nazim.sh", "clear Nazim (console)"),
    "cai":   ("scripts/reset_cai.sh",   "clear cai (governance)"),
    "hub":   ("scripts/reset_hub_remote.sh", "clear the hub (VPS)"),
}

# Reset idempotency guard (op#8989 — parity with the Telegram-button fix). The
# console is a ThreadingHTTPServer, so a double-click or a retried POST fires two
# near-simultaneous /api/reset calls; without a guard each runs the reset script
# and re-creates the double-/clear the operator caught. Per-body claim, atomic
# under _RESET_GUARD_LOCK: (a) an in-flight body rejects a concurrent 2nd call for
# the whole run (covers a slow reset like the hub SSH); (b) a post-completion
# cooldown rejects an impatient sequential re-click. A deliberate re-reset after
# the cooldown still works. In-process is sufficient — single server process.
_RESET_GUARD_LOCK = threading.Lock()
_RESET_INFLIGHT: set[str] = set()
_RESET_LAST_RUN: dict[str, float] = {}
_RESET_COOLDOWN_S = 60.0

# --- Lane OAuth-account switch (token-pool conservation) ----------------------
# Re-token a lane onto a DIFFERENT terms-clean Claude Max account (Musa/Syed) by
# killing + relaunching it via scripts/switch_lane_token.sh (kill + relaunch is
# the ONLY way — a lane's account is fixed at process launch). Same auth gate as
# /api/reset (this is a mutating POST, IP-allowlist + breakglass), same
# shell-out-to-a-vetted-script pattern (the console DB session is read-only), and
# an ALLOWLIST that maps a token NAME -> its 0600 key file.
#
# GOVERNANCE (cai ruling CAI-729): the gazzabyte consumer Max token is FORBIDDEN
# for lane use. It is excluded here at TWO layers (defense in depth): it is not a
# key in LANE_TOKEN_FILES, and switch_lane_token.sh independently refuses the
# gazzabyte-oauth-token basename. Never add it to this map.
#
# HOSTED CONSOLE: this endpoint is Mini-console / strongly-authed ONLY. The public
# read-only hosted console (nervous_system/console/hosted_server.py) MUST refuse
# /api/switch-token (it 403s it alongside /api/reset + /api/backlog) so it can
# never drive a lane restart.
_KEYS_DIR = pathlib.Path(os.path.expanduser("~/.wingmen/keys"))
_MUSA_TOKEN_FILE = _KEYS_DIR / "musa-oauth-token"
LANE_TOKEN_FILES: dict[str, pathlib.Path] = {
    # Terms-clean accounts only. gazzabyte deliberately ABSENT (CAI-729).
    "musa": _MUSA_TOKEN_FILE,
    "syed": _KEYS_DIR / "syed-oauth-token",
}
_FORBIDDEN_TOKEN_BASENAMES = {"gazzabyte-oauth-token"}
_LANE_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Switch idempotency guard — one re-token per lane at a time (a double-click must
# never fire two kills). Shares the reset lock; separate in-flight set + cooldown.
_SWITCH_INFLIGHT: set[str] = set()
_SWITCH_LAST_RUN: dict[str, float] = {}
_SWITCH_COOLDOWN_S = 30.0


def _resolve_lane_token_file(token_name: str) -> pathlib.Path | None:
    """Map an allowlisted token NAME -> a readable 0600 key file, or None.

    - Unknown/forbidden name -> None (fail closed; gazzabyte is not a key here).
    - "musa": the token lives in orchestrator .env (CLAUDE_CODE_OAUTH_TOKEN), not
      a file yet, so materialize a 0600 key file from the process env on first
      use (dotenv loaded it at startup). Every other account already has a file.
    - Refuse a resolved path whose basename is on the forbidden list (belt +
      suspenders — the shell script checks this too).
    """
    path = LANE_TOKEN_FILES.get(token_name)
    if path is None:
        return None
    if path.name in _FORBIDDEN_TOKEN_BASENAMES:
        return None
    if token_name == "musa" and not path.exists():
        tok = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        if not tok:
            logger.warning("switch-token: cannot materialize musa key file — "
                           "CLAUDE_CODE_OAUTH_TOKEN absent from console env")
            return None
        try:
            _KEYS_DIR.mkdir(parents=True, exist_ok=True)
            # 0600 BEFORE writing the secret (open with restrictive mode).
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(tok + "\n")
            os.chmod(path, 0o600)
            logger.info("switch-token: materialized musa key file (0600) at %s", path)
        except Exception as e:  # noqa: BLE001
            logger.warning("switch-token: failed to materialize musa key file: %s", e)
            return None
    try:
        if not os.access(path, os.R_OK):
            return None
    except Exception:  # noqa: BLE001
        return None
    return path


# --- R2b: pointer/registry CRUD (op#10706) — reversible, NO relaunch ----------
# The /lanes page lets the operator SELECT a token + model per body. These write
# the per-body POINTER FILES only (the DEFAULT a body boots with) — they DO NOT
# relaunch anything and DO NOT change any live billing (that's the gated R3/R4
# apply path). Every write is rm-reversible. gazzabyte stays fail-closed (it is
# not in LANE_TOKEN_FILES, so it can never be selected). The console DB session
# is READ-ONLY, so the audit sink here is auth.audit (file log), not a bus row.
_MODEL_ALLOWLIST = (
    "claude-opus-4-8", "claude-opus-5", "claude-sonnet-5",
    "claude-fable-5", "claude-haiku-4-5",
)
# Which token-pointer file governs a body's default account. Console + hub own
# theirs; every worker lane shares .lane_default_token (there is no per-lane token
# pointer — selecting a token on a lane sets the ALL-LANES default). cai + the SRE
# boot straight off .env, so their token default is NOT pointer-settable here.
_TOKEN_POINTER_FOR = {"nazim": ".nazim_default_token", "cc-orchestrator": ".orch_default_token"}
_NO_TOKEN_POINTER = {"cai", "fleet-health"}   # boot off .env; token not settable via pointer
# MODEL is honored via the .<session>_model pointer ONLY by launch_dangerous_cc.sh
# (worker lanes). These bodies set their model from an ENV var at boot instead
# (NAZIM_MODEL / CAI_MODEL / MODEL / ORCH_MODEL), so a model-pointer write would be
# a SILENT NO-OP for them — the UI must mark model not-settable + the API refuse it
# (fail loud, op#10706 R2b fix (a)).
_MODEL_ENV_BODIES = {"nazim", "cai", "fleet-health", "cc-orchestrator"}
_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _is_remote_body(session: str) -> bool:
    """A body whose pointer files live on ANOTHER host (the VPS hub): a local
    write is a silent no-op, so it is not settable from this console."""
    return session in getattr(panes, "_REMOTE_BODIES", {})


def _token_pointer_name(session: str) -> "str | None":
    """The token-pointer FILENAME governing this body's default account, or None
    when the body has no settable pointer (cai/SRE run off .env)."""
    if session in _NO_TOKEN_POINTER:
        return None
    return _TOKEN_POINTER_FOR.get(session, ".lane_default_token")


def _read_pointer_target(name: str) -> "str | None":
    """The token-file PATH a pointer file currently points at (stripped), or None."""
    try:
        p = _REPO_ROOT / name
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or None
    except Exception:  # noqa: BLE001
        pass
    return None


def _fp_of_token_file(path: str) -> "str | None":
    """sha256[:12] of the token in a file (raw token stays local), or None."""
    try:
        tok = pathlib.Path(os.path.expanduser(path)).read_text(encoding="utf-8").strip()
        return hashlib.sha256(tok.encode("utf-8")).hexdigest()[:12] if tok else None
    except Exception:  # noqa: BLE001
        return None


_TOKEN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_TOKEN_FILE_SUFFIX = "-oauth-token"


def _discover_tokens() -> "dict":
    """{name: path} of all selectable token key files — the built-ins (musa/syed)
    PLUS any *-oauth-token the operator added to ~/.wingmen/keys (op#10706 add-token),
    so added tokens persist + appear without a code change. gazzabyte is excluded by
    basename (CAI-729). Used by the REVERSIBLE R2b pointer/registry path only; the
    ARMED /api/switch-token keeps its own hardcoded LANE_TOKEN_FILES allowlist."""
    found = {}
    m = _resolve_lane_token_file("musa")   # materializes musa from .env if needed
    if m:
        found["musa"] = m
    try:
        for f in sorted(_KEYS_DIR.glob("*" + _TOKEN_FILE_SUFFIX)):
            if f.name in _FORBIDDEN_TOKEN_BASENAMES:
                continue
            if not os.access(f, os.R_OK):
                continue
            name = f.name[: -len(_TOKEN_FILE_SUFFIX)]
            if _TOKEN_NAME_RE.match(name):
                found[name] = f
    except Exception:  # noqa: BLE001
        pass
    return found


def _resolve_registry_token(name: str) -> "pathlib.Path | None":
    """Resolve a registry token NAME -> readable key file for the R2b pointer path.
    gazzabyte / forbidden basenames fail closed."""
    f = _discover_tokens().get(name)
    if f is None or f.name in _FORBIDDEN_TOKEN_BASENAMES:
        return None
    return f if os.access(f, os.R_OK) else None


def _token_registry() -> list:
    """The selectable token accounts (name + fp, NEVER the raw token). gazzabyte is
    excluded by construction. Discovers operator-added tokens too."""
    out = []
    for name, f in sorted(_discover_tokens().items()):
        out.append({"name": name, "fp": _fp_of_token_file(str(f)), "available": True})
    return out


# --- R4 ARMED APPLY (op#10706, CAI-RESP-746/747 tiered arm) — DISABLED/unbuilt-live ---
# The ONLY path that performs a REAL armed relaunch (re-token / model-apply). It
# ships DISABLED and is provably inert three ways, ALL required:
#   1. Feature flag OFF by default: CONSOLE_R4_ENABLED != "1" -> the endpoint 503s.
#      Turning it on is gated on cai sign-off + Nazim review (NOT mine to flip).
#   2. A LIVE bridge-verified operator ARM must exist — an INBOUND operator Telegram
#      approval (require_verified_authorization), scoped + time-bounded. The console
#      only READS it (read-only DB); it NEVER writes a grant (ops-not-governance).
#   3. Per-apply: a typed body-name confirm (fat-finger guard) + gazzabyte fail-closed
#      + routes through the SAME switch script (one rail, cai cond 1) which verifies
#      auth_fp after + emits the identity-stamped audit/alert (cai conds 2/3/4).
# I NEVER arm it. This block builds the confirm LAYER only, for cai+Nazim review.
_R4_ENABLED = os.environ.get("CONSOLE_R4_ENABLED") == "1"
_R4_ARM_MAX_WINDOW_MIN = 240          # hard cap on an arm's life (auto-disarm)
_R4_ARM_PHRASE = "ARM LANE APPLY"      # the operator's Telegram approval phrase


def _r4_current_arm() -> "dict | None":
    """The live, bridge-verified operator ARM, or None (fail-closed). READ-ONLY:
    a real INBOUND operator Telegram approval in operator_messages within a
    time-bounded window. Scoped to the token account NAMES the approval names
    (gazzabyte can NEVER be in scope). The console never writes the arm."""
    try:
        import sys as _sys, datetime as _dt, re as _re
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from scripts.lib.require_verified_authorization import verified_authorization
        now = _dt.datetime.now(_dt.timezone.utc)
        after = now - _dt.timedelta(minutes=_R4_ARM_MAX_WINDOW_MIN)
        res = verified_authorization(
            op_id="lane-apply-arm", after=after,
            approval_phrases=[_R4_ARM_PHRASE], op_tokens=["arm", "lane", "apply"])
        if not res.ok or not res.row:
            return None
        row = res.row
        text = (row.get("text") or "").lower()
        toks = text.split()
        # cai CAI-RESP-748 (a) — REJECT-ON-MALFORMED, fail-closed, NEVER default the
        # scope or TTL. gazzabyte requested (name/basename) anywhere -> whole arm void.
        if "gazzabyte" in text or "gazzabyte-oauth-token" in text:
            return None
        # minutes: a standalone integer token is REQUIRED (missing/unparseable ->
        # no arm); > the 240m cap -> REJECT (do NOT silently clamp — operator restates).
        mins = next((int(t) for t in toks if t.isdigit()), None)
        if mins is None or mins <= 0 or mins > _R4_ARM_MAX_WINDOW_MIN:
            return None
        # accounts: >=1 terms-clean allowlisted account named as a token; else no arm.
        allow = {e["name"] for e in _token_registry() if e["name"] != "gazzabyte"}
        accounts = sorted(a for a in allow if a in toks)
        if not accounts:
            return None
        created = row.get("created_at")
        if isinstance(created, str):
            created = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=_dt.timezone.utc)
        expires = created + _dt.timedelta(minutes=mins)
        if now >= expires:
            return None
        return {"expires_at": expires.isoformat(), "accounts": accounts,
                "granted_row_id": row.get("id")}
    except Exception:  # noqa: BLE001 — any uncertainty -> no arm (fail-closed)
        return None


def _resolve_armed_apply(session: str, kind: str, arm: "dict | None") -> tuple:
    """Resolve the switch command + target account for an armed apply, enforcing
    the SAME scope/gazzabyte/target rules for an immediate AND a queued fire.
    Returns (cmd_list, account_name, None) or (None, None, error_str)."""
    if _is_remote_body(session):
        return None, None, "remote body — armed apply runs on the VPS"
    SW_LANE = str(_REPO_ROOT / "scripts" / "switch_lane_token.sh")
    SW_SINGLE = str(_REPO_ROOT / "scripts" / "switch_singleton_token.sh")
    if kind == "token":
        ptr = _token_pointer_name(session)
        target = _read_pointer_target(ptr) if ptr else None
        if not target or not _fp_of_token_file(target):
            return None, None, "no token default set to apply"
        if os.path.basename(target) in _FORBIDDEN_TOKEN_BASENAMES:
            return None, None, "forbidden token (gazzabyte)"
        account = next((e["name"] for e in _token_registry()
                        if e["fp"] == _fp_of_token_file(target)), None)
        if arm and arm.get("accounts") and account not in arm["accounts"]:
            return None, account, f"account '{account}' not in arm scope"
        cmd = ["bash", (SW_SINGLE if session == "nazim" else SW_LANE), session, target]
        return cmd, account, None
    if kind == "model":
        if session in _MODEL_ENV_BODIES:
            return None, None, "model not pointer-settable for this body"
        cur = _current_token_file_for(session)
        if cur is None or cur.name in _FORBIDDEN_TOKEN_BASENAMES:
            return None, None, "could not resolve current account (or forbidden)"
        account = next((e["name"] for e in _token_registry()
                        if e["fp"] == _fp_of_token_file(str(cur))), None)
        if arm and arm.get("accounts") and account not in arm["accounts"]:
            return None, account, f"account '{account}' not in arm scope"
        return ["bash", SW_LANE, "--model-apply", session, str(cur)], account, None
    return None, None, "bad kind"


# --- Queue-on-busy apply (op#10861) ------------------------------------------
# When an armed apply targets a BUSY lane, QUEUE it instead of refusing; a watcher
# fires it the moment the lane goes idle. NEVER --force (waits, never clobbers).
# HARD GUARDRAIL: the R4 arm is RE-VALIDATED AT FIRE TIME — a queued apply NEVER
# fires on an expired/out-of-scope arm (keeps operator authorization honest for a
# deferred fire). In-memory (single console process); a queue entry is ephemeral —
# lost on restart, the operator re-queues. Only meaningful while R4 is enabled +
# armed. Keyed by "session:kind".
_APPLY_QUEUE: "dict" = {}
_APPLY_QUEUE_LOCK = threading.Lock()
_APPLY_QUEUE_POLL_S = 12.0


def _lane_busy(session: str) -> bool:
    """True if the lane is working (mid-turn). Unknown -> treat as busy (fail-safe:
    never fire a queued apply on a lane we can't confirm is idle)."""
    try:
        state, _ = panes.capture(session)
        return state != "idle"
    except Exception:  # noqa: BLE001
        return True


def _apply_queue_public() -> list:
    with _APPLY_QUEUE_LOCK:
        return [dict(v) for v in _APPLY_QUEUE.values()]


def _fire_queued(item: dict, cmd: list) -> None:
    """Run a queued armed apply (idle + arm re-validated by the caller). ARMED=1 so
    the switch script verifies auth_fp + audits/alerts. Never --force."""
    key = f"{item['session']}:{item['kind']}"
    try:
        _env = {**os.environ, "ARMED": "1", "BREAK_GLASS": "0", "ACTOR": item.get("actor", "operator")}
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=_env)
        ok = r.returncode == 0
        with _APPLY_QUEUE_LOCK:
            if key in _APPLY_QUEUE:
                _APPLY_QUEUE[key]["status"] = "applied" if ok else "failed"
                _APPLY_QUEUE[key]["note"] = ("applied on idle" if ok else "switch failed — see audit")
        logger.info("queued apply FIRED %s (ok=%s)", key, ok)
    except Exception as e:  # noqa: BLE001
        logger.warning("queued apply fire failed %s: %s", key, e)
        with _APPLY_QUEUE_LOCK:
            if key in _APPLY_QUEUE:
                _APPLY_QUEUE[key]["status"] = "failed"
                _APPLY_QUEUE[key]["note"] = "fire error"


def _apply_queue_tick() -> None:
    """One watcher pass: for each queued apply, RE-VALIDATE the arm (hard guardrail),
    then fire iff the lane is idle. Fail-closed on an expired/out-of-scope arm."""
    with _APPLY_QUEUE_LOCK:
        pending = [dict(v) for v in _APPLY_QUEUE.values() if v.get("status") == "queued"]
    for item in pending:
        key = f"{item['session']}:{item['kind']}"
        arm = _r4_current_arm()                      # RE-VALIDATE at fire time
        cmd, _account, err = _resolve_armed_apply(item["session"], item["kind"], arm)
        if not arm or err or cmd is None:
            reason = ("arm expired while waiting — re-arm to apply" if not arm
                      else (err or "cannot apply"))
            with _APPLY_QUEUE_LOCK:
                if key in _APPLY_QUEUE and _APPLY_QUEUE[key]["status"] == "queued":
                    _APPLY_QUEUE[key]["status"] = "arm-expired"
                    _APPLY_QUEUE[key]["note"] = reason
            logger.info("queued apply HELD %s: %s", key, reason)
            continue
        if _lane_busy(item["session"]):
            continue                                 # still busy — keep waiting
        _fire_queued(item, cmd)                      # idle + armed + in-scope -> fire


def _apply_queue_watcher() -> None:
    while True:
        try:
            _apply_queue_tick()
        except Exception as e:  # noqa: BLE001 — a watcher must never die
            logger.warning("apply-queue watcher tick error: %s", e)
        time.sleep(_APPLY_QUEUE_POLL_S)


def _current_token_file_for(session: str) -> "pathlib.Path | None":
    """The registry token file whose fp == the body's LIVE account — for a MODEL-
    apply that must relaunch on the SAME account. None if unresolved."""
    try:
        rows = panes.token_ground_truth(include_remote=False).get("rows", [])
        fp = next((r.get("fp") for r in rows
                   if r.get("session") == session and r.get("fp")), None)
    except Exception:  # noqa: BLE001
        fp = None
    if not fp:
        return None
    for _n, f in _discover_tokens().items():
        if _fp_of_token_file(str(f)) == fp:
            return f
    return None


def _read_model_pointer(session: str) -> "str | None":
    """The per-body model pointer value (.<session>_model), or None if unset."""
    if not _SESSION_RE.match(session or ""):
        return None
    try:
        p = _REPO_ROOT / (".%s_model" % session)
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or None
    except Exception:  # noqa: BLE001
        pass
    return None


def _token_name_for_fp(fp: "str | None") -> "str | None":
    """Reverse a registry token fp -> its name (so the UI can preselect)."""
    if not fp:
        return None
    for e in _token_registry():
        if e.get("fp") == fp:
            return e["name"]
    return None


def _enrich_token_pointers(payload: dict) -> dict:
    """Fold per-body pointer state into a token_ground_truth payload so /lanes can
    show + preselect the current token/model defaults. Read-only."""
    for r in payload.get("rows", []):
        sess = r.get("session")
        remote = _is_remote_body(sess)
        ptr = _token_pointer_name(sess)
        r["remote"] = remote
        # Settable HERE only if the write actually takes effect: a local pointer
        # this host's boot honors, and not a remote (VPS) body. Otherwise the UI
        # shows a note instead of a select — never a silent no-op (fix (a)).
        r["token_settable"] = (ptr is not None) and not remote
        r["token_pointer"] = ptr
        r["token_pointer_name"] = _token_name_for_fp(
            _fp_of_token_file(_read_pointer_target(ptr)) if ptr and _read_pointer_target(ptr) else None)
        r["model_settable"] = (sess not in _MODEL_ENV_BODIES) and not remote
        r["model_pointer"] = _read_model_pointer(sess)
    payload["registry"] = {"tokens": _token_registry(), "models": list(_MODEL_ALLOWLIST)}
    payload["apply_queue"] = _apply_queue_public()   # op#10861 queued/held applies
    return payload


def _build_version() -> str:
    """The build identity shown in the version badge + reported by /api/version.
    Single source of truth = the VERSION baked into sw.js (the same constant
    that names the SW cache), read once at startup. So there is exactly ONE
    place to bump per deploy."""
    try:
        sw = (_STATIC_DIR / "sw.js").read_text(encoding="utf-8")
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', sw)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "unknown"


def _build_sha() -> str:
    """Short git SHA of the DEPLOYED tree, resolved once at process start — so a
    kickstart after a deploy reports the new commit, and the operator can see on
    his device exactly which commit he's running."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


_BUILD_VERSION = _build_version()
_BUILD_SHA = _build_sha()


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


def _enrich_lanes_live(rows, live=None):
    """Fold a LIVE tmux summary into each lane row (shared by /api/lanes and
    /api/fleet) so a card shows — and is CLASSIFIED by — what the lane is
    ACTUALLY doing now (working/idle from the live pane), not the stale
    agent_status.status / boot-string current_task (which reads 'active' +
    task=None for every lane, the '0 working' bug 2026-07-11).

    The tmux state is the truth: pane running + 'esc to interrupt'/spinner =
    working; running + idle prompt = idle; no live pane = offline. Two refinements
    keep the COUNT honest:
      * one `list-sessions` for the whole sweep (not one per lane); and
      * each live session is OWNED by the freshest-heartbeat lane targeting it,
        so two rows sharing a session label (e.g. cc-infra-1 & the stale
        cc-infra-2, both -> 'infra') don't both count as working — only the
        live owner does; the stale twin reads offline.

    `live` lets the caller pass a pre-fetched local live-session set (so the
    whole /api/fleet sweep does ONE list-sessions, shared with the coordinator
    peekable-check), matching panes.capture's own `live` optimization."""
    if live is None:
        live = set(panes.live_sessions())

    # 1) resolve each row's target tmux session (per-instance tmux_session wins;
    #    the orchestrator doesn't self-register, so target its known 'orch').
    for r in rows:
        sess = r.get("tmux_session") or r.get("lane")
        if not r.get("tmux_session") and str(r.get("agent_id", "")).startswith("cc-orchestrator"):
            sess = "orch"
            r["tmux_session"] = "orch"
        r["_sess"] = sess

    # 2) ownership: freshest heartbeat wins a contested live session.
    def _hb(r):
        h = r.get("heartbeat_age_s")
        return h if h is not None else 10 ** 9
    owner = {}
    for r in sorted(rows, key=_hb):
        s = r.get("_sess")
        if s and s in live and s not in owner:
            owner[s] = r.get("agent_id")

    # 3) capture each owned live session ONCE; non-owners / no pane -> offline.
    cache = {}
    for r in rows:
        s = r.pop("_sess", None)
        if not s or s not in live or owner.get(s) != r.get("agent_id"):
            r["live"] = {"running": False}
            continue
        if s not in cache:
            state, _txt = panes.capture(s, live=live)
            cache[s] = state or {"running": False}
        r["live"] = cache[s]
    return rows


def _lane_bucket(l):
    """working / idle / offline / flagged classification for a lane row. The
    LIVE tmux pane is authoritative (operator 2026-07-11: agent_status.status
    reads 'active' + task=None for every lane, so it can't tell working from
    idle — the '0 working' bug). So: live pane working -> working; live pane
    idle -> idle; NO live pane -> offline. Heartbeat/desired_state no longer
    decide the bucket, only whether to FLAG it: a lane the fleet WANTS up
    (desired_state='up') but that has no live pane is dark and needs attention."""
    desired_up = str(l.get("desired_state") or "").lower() == "up"
    live = l.get("live") or {}
    running = bool(live.get("running"))
    if running and live.get("state") == "working":
        state = "working"
    elif running:
        state = "idle"
    else:
        state = "offline"
    flagged = desired_up and state == "offline"  # supposed to be up, but dark
    return state, flagged


# Cross-host coordinators (bodies that don't run on THIS console host) are
# peeked via a DB read of their bus activity, NOT ssh — keeps the console's
# surface local read-only (operator #3729). Maps peek session -> coordinator
# from_agent. A session name only ever LOOKS UP here; unknown names fall through.
# The hub ('orch' session) runs on the VPS, so like Nazim it's peeked via the DB:
# a fresh published pane if the VPS-side coordinator_pane_publisher is up, else a
# fall-back bus-activity feed (fetch_coordinator_peek handles the switch). Added
# op 2026-08-02 — "make sure orch's context is displayed. i cant reset what i
# cant see."
_COORD_DB_PEEK = {"nazim": "orch-console", "orch": "cc-orchestrator"}


# Soft budget for the whole /api/fleet aggregate. Over this we log a warning so
# a slow-query regression is caught in the log before it's caught on the phone
# (the 2026-07-11 regression showed up as a client-side timeout, not a metric).
_FLEET_BUDGET_MS = int(os.environ.get("CONSOLE_FLEET_BUDGET_MS", "500"))

# Context-bloat normalization: an always-on agent's CURRENT context size
# (latest_context_tokens = the last turn's input+cache_read+cache_creation)
# ÷ the model context window => 0-100%. Opus 4.8 runs a 1M-token window here
# (verified via /context; the model's max_input_tokens is 1,000,000 — NOT 2M).
# Overridable via CONSOLE_CTX_WINDOW. Soft/hard thresholds (green→amber→red) at
# 60% (~600K) / 80% (~800K).
_CTX_WINDOW = int(os.environ.get("CONSOLE_CTX_WINDOW", "1000000"))
_CTX_SOFT = 0.60
_CTX_HARD = 0.80

# Stale-reading drop (op#9770 "never show stale info"): a context-bloat row whose
# freshest reading is older than this is a DEAD identity's frozen number (e.g. the
# 'cc-ihsanos hb 10d ago' phantom), not a live window-fill signal — drop it from
# the list. Generous by design: an always-on body writes session costs continually,
# so hours-of-silence still shows; only genuinely-gone identities (default >48h)
# are dropped. Tunable without a redeploy via CONSOLE_CTX_STALE_DROP_S.
_CTX_STALE_DROP_S = int(os.environ.get("CONSOLE_CTX_STALE_DROP_S", str(48 * 3600)))

# Dead-lane drop (op#9770): after live-pane enrichment, a lane with NO live pane
# (offline) whose heartbeat is older than this AND that nobody expects up (not
# flagged) is a dead instance, not an idle one — drop it so it never renders.
# A working/idle lane (live pane) is ALWAYS kept regardless of heartbeat age, and
# a flagged lane (desired_state=up but dark) is ALWAYS kept (the operator needs to
# see it). Tunable via CONSOLE_LANE_STALE_DROP_S.
_LANE_STALE_DROP_S = int(os.environ.get("CONSOLE_LANE_STALE_DROP_S", str(6 * 3600)))

# The three coordinator brains (op#9088). Their context now lives ON their own
# cards in the Coordinators section, so they are FILTERED OUT of the context-bloat
# list (which is worker lanes only). Instances (e.g. 'orch-console-2') are caught
# by the prefix check too, so a re-registered twin can't leak back into the list.
_COORD_IDENTITIES = ("cai", "cc-orchestrator", "orch-console", "cc-fleet-health")


def _is_coord_identity(name) -> bool:
    n = str(name or "")
    return any(n == k or n.startswith(k + "-") for k in _COORD_IDENTITIES)


def _ctx_level(ctx):
    """(pct, level) of the model window for a current-context token count, or None
    if the value is missing / non-positive / impossibly large (> the window — a
    single turn cannot exceed the context window). Shared by the context-bloat
    list AND the per-coordinator context readout so both use the SAME window +
    green/amber/red thresholds."""
    if ctx is None or _CTX_WINDOW <= 0:
        return None
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        return None
    if ctx <= 0 or ctx > _CTX_WINDOW:
        return None
    frac = ctx / _CTX_WINDOW
    pct = round(min(frac, 1.0) * 100)
    level = "red" if frac >= _CTX_HARD else ("amber" if frac >= _CTX_SOFT else "green")
    return pct, level


def _context_bloat(rows):
    """Annotate each latest-per-WORKER-LANE row with CURRENT-context pct (of the
    model window) + a green/amber/red level, sorted fullest-first. `ctx_tokens` is
    the live window fill (the last turn's actual input context). Rows whose value
    is missing, non-positive, or impossibly large are DROPPED as bad data. The
    three coordinators are EXCLUDED — their context shows on their own coordinator
    cards now (op#9088). `auth_fp` rides through so each row shows the same 🔑
    token badge as the lane cards; `age_s` surfaces a stale reading."""
    out = []
    if _CTX_WINDOW <= 0:
        return out
    for r in rows or []:
        if _is_coord_identity(r.get("cc_identity")):
            continue  # coordinators live in the Coordinators section
        age = r.get("age_s")
        if age is not None and age > _CTX_STALE_DROP_S:
            continue  # dead identity's frozen reading (op#9770) — never show stale
        lvl = _ctx_level(r.get("ctx_tokens"))
        if lvl is None:
            continue  # bad data — cannot be a real current-context reading
        pct, level = lvl
        out.append({
            "agent": r.get("cc_identity"),
            # obs-1 (op#10550): the instance discriminator within a family
            # (sub_tag == the instance's tmux_session; NULL for a solo lane). The
            # client keys laneCtxIndex by (agent, sub_tag) so each perimeter card
            # folds in ITS OWN gauge instead of a shared one.
            "sub_tag": r.get("sub_tag"),
            "ctx_tokens": int(r.get("ctx_tokens")),
            "window": _CTX_WINDOW,
            "pct": pct,
            "level": level,
            "age_s": r.get("age_s"),
            "auth_fp": r.get("auth_fp"),
            "host": r.get("host"),
        })
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


def _fleet_payload():
    """The single live-derived aggregate behind /api/fleet (redesign #7576):
    pulse counts + needs-you hero + exception-first flagged lanes + working-first
    lanes + deploys, all from real tables. One round-trip for the phone.

    The three reads are independent, so they fire CONCURRENTLY on the warm
    connection pool (db._query reuses connections): wall-clock is one query RTT
    (~100ms) instead of three serial ones. The tmux pane enrichment runs after
    the lanes read returns (it needs those rows)."""
    t0 = time.perf_counter()
    # ONE list-sessions for the whole aggregate: the lane enrichment and the
    # coordinator peekable-check share it (a subprocess each would otherwise
    # cost ~2x). The 4 reads are independent -> fire concurrently.
    live = set(panes.live_sessions())
    with ThreadPoolExecutor(max_workers=8) as ex:
        f_lanes = ex.submit(db.fetch_lanes)
        f_deploys = ex.submit(db.fetch_deploys)
        f_needs = ex.submit(db.fetch_needs_you)
        f_coord = ex.submit(db.fetch_coordinators)
        # operator-visibility signals, fired concurrently:
        f_backlog = ex.submit(db.fetch_backlog)          # operator's "Your asks" tracker
        f_bloat = ex.submit(db.fetch_context_bloat)      # per-agent context %
        f_pool = ex.submit(db.fetch_pool_usage)          # Max weekly-% per pool (op#9770)
        f_queue = ex.submit(db.fetch_queue)              # per-lane worklist (lane_tasks — the drain view)
        lanes = _enrich_lanes_live(f_lanes.result(), live=live)
        deploys = f_deploys.result()
        # Operator-audience needs ONLY. The 'fleet' audience fed a "Fleet is
        # handling" section (hub-owned items the operator can't action) AND was
        # inflating the "N things need you" pulse while "Needs you" showed
        # nothing — operator 2026-08-02: "this section seems irrelevant". Dropping
        # them here hides the section (frontend auto-hides an empty fleet group)
        # and makes the pulse count match reality. Reversible: delete this filter.
        needs = [n for n in f_needs.result() if n.get("audience") == "operator"]
        coordinators = f_coord.result()
        # each guarded: a signal failing must not blank the whole aggregate.
        try:
            backlog = f_backlog.result()
        except Exception as e:
            logger.warning("backlog failed: %s", e)
            backlog = []
        try:
            context_bloat = _context_bloat(f_bloat.result())
        except Exception as e:
            logger.warning("context_bloat failed: %s", e)
            context_bloat = []
        try:
            pool_usage = f_pool.result()
        except Exception as e:
            logger.warning("pool_usage failed: %s", e)
            pool_usage = []
        try:
            queue = f_queue.result()
        except Exception as e:
            logger.warning("queue failed: %s", e)
            queue = []
    # Mobile payload trim (op#10291): the queue `detail` field was ~11KB of the
    # ~18KB queue section (63%) — the bulk of the /api/fleet payload that the
    # marginal Abu-Dhabi<->Singapore relay drops. It's only needed ON TAP, which
    # the dedicated /api/queue endpoint serves in full — so truncate it to a short
    # preview HERE (the overview) only. Cuts the payload ~9KB with no UI loss (the
    # inline preview still shows; the full text loads from /api/queue when tapped).
    _qd = int(os.environ.get("CONSOLE_QUEUE_DETAIL_PREVIEW", "80"))
    for _t in queue:
        d = _t.get("detail")
        if isinstance(d, str) and len(d) > _qd:
            _t["detail"] = d[:_qd].rstrip() + "…"

    # A coordinator card is peekable when its pane is a LOCAL live tmux session
    # (orch on this Studio host) OR it's a cross-host coordinator we surface via a
    # DB-read activity feed (Nazim on the Mini — reverted from ssh to a DB read,
    # operator #3729). The DB source is always available, so no reachability probe.
    for c in coordinators:
        sess = c.get("tmux_session")
        c["peekable"] = bool(sess and (sess in live or sess in _COORD_DB_PEEK))
        # Each coordinator card carries its OWN context readout (op#9088), from
        # the same source + thresholds as the context-bloat list.
        lvl = _ctx_level(c.get("ctx_tokens"))
        c["ctx_pct"], c["ctx_level"] = (lvl if lvl is not None else (None, None))

    # Dead-lane drop (op#9770): now that the live pane has classified each row,
    # drop a lane that is offline (no live pane) AND heartbeat-stale AND not
    # flagged (nobody expects it up) — a dead instance, never an idle one. Done
    # HERE (post-enrichment), not in SQL, so a working lane with a stalled
    # heartbeat writer is kept (the live pane, not the heartbeat, is the truth).
    kept = []
    for l in lanes:
        state, flagged = _lane_bucket(l)
        hb = l.get("heartbeat_age_s")
        if (state == "offline" and not flagged
                and hb is not None and hb > _LANE_STALE_DROP_S):
            continue  # dead instance — never render (op#9770)
        l["_state"], l["_flagged"] = state, flagged
        kept.append(l)
    lanes = kept

    counts = {"working": 0, "idle": 0, "offline": 0, "flagged": 0}
    for l in lanes:
        state, flagged = l.pop("_state"), l.pop("_flagged")
        l["bucket"] = state
        l["flagged"] = flagged
        counts[state] = counts.get(state, 0) + 1
        if flagged:
            counts["flagged"] += 1

    # working first, then idle, then offline; flagged floats up within its group.
    order = {"working": 0, "idle": 1, "offline": 2}
    lanes.sort(key=lambda l: (0 if l["flagged"] else 1, order.get(l["bucket"], 3)))

    elapsed_ms = (time.perf_counter() - t0) * 1000
    if elapsed_ms > _FLEET_BUDGET_MS:
        logger.warning(
            "fleet payload over budget: %.0fms > %dms (%d lanes)",
            elapsed_ms, _FLEET_BUDGET_MS, len(lanes),
        )

    return {
        "pulse": {**counts, "needs_you": len(needs)},
        "needs_you": _jsonable(needs),
        "backlog": _jsonable(backlog),
        "coordinators": _jsonable(coordinators),
        "lanes": _jsonable(lanes),
        "deploys": _jsonable(deploys),
        "context_bloat": _jsonable(context_bloat),
        "pool_usage": _jsonable(pool_usage),
        "queue": _jsonable(queue),
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

            # Build identity: OPEN (like /healthz), non-sensitive (version + short
            # SHA). Open so the version badge renders even off-tailnet / pre-auth
            # — the operator must always be able to see which build he's on, which
            # is the whole point of the badge (PWA-cache-loop fix 2026-07-11).
            if path == "/api/version":
                auth.audit(self._client(), path, "200")
                return self._json(200, {"version": _BUILD_VERSION, "sha": _BUILD_SHA})

            # Attention-first Fleet view (redesign #7576) is now the DEFAULT
            # console (operator #3440 — "remove the classic version"). It serves
            # "/" and "/fleet"; the classic message/deploy console is retained
            # (its full lists are still reachable) at "/classic".
            if path in ("/", "/index.html", "/fleet", "/fleet/"):
                return self._serve_static("fleet.html", path)

            if path in ("/classic", "/classic/"):
                return self._serve_static("index.html", path)

            # Dedicated MOBILE-FIRST lane-management page (op#10706 B): the token+
            # model ground-truth tool on its OWN route (operator eyeballs on his
            # phone). Open SPA shell like /fleet; it fetches /api/token-truth with
            # the stored bearer token. Kept OFF /api/fleet so its remote-hub SSH
            # scan never slows the main fleet payload.
            if path in ("/lanes", "/lanes/"):
                return self._serve_static("lanes.html", path)

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

                if path == "/api/token-truth":
                    # Process-verified per-body token + model (op#10706/10715/10717),
                    # incl. the remote VPS hub via SSH (op#10706 C). Its own endpoint
                    # (feeds the /lanes page) so the ~SSH latency stays off /api/fleet.
                    # Enriched with per-body pointer state + the registry (R2b) so the
                    # page can show + preselect the current token/model defaults.
                    payload = _enrich_token_pointers(panes.token_ground_truth(include_remote=True))
                    auth.audit(self._client(), path, "200")
                    return self._json(200, payload)

                if path.startswith("/api/lanes/") and path.endswith("/pane"):
                    # Live tmux pane peek (read-only) — operator-requested,
                    # thread f869956c/msg 6156. A LOCAL session is validated
                    # against the real live tmux list inside capture_pane; a
                    # cross-host coordinator (Nazim on the Mini) is served from a
                    # DB read of its bus activity instead of ssh (operator #3729).
                    # An unrecognized name matches neither -> 404, no subprocess.
                    session = unquote(path[len("/api/lanes/"):-len("/pane")])
                    coord_agent = _COORD_DB_PEEK.get(session)
                    if coord_agent is not None:
                        text = db.fetch_coordinator_peek(coord_agent) or None
                    else:
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

        def do_POST(self):  # noqa: N802
            """The ONLY mutating surface on the console: an operator-triggered
            context reset (op#8910). POST-only so a GET/prefetch/link can never
            clear a body; operator-authed (IP-allowlist + breakglass token, same
            gate as every /api route); allowlist-gated so an unknown body id fails
            closed. The reset scripts themselves are the safety net (BUSY-refuse,
            composer-preserve, handoff gate) — this only TRIGGERS the proven path
            the Telegram clear-buttons already use."""
            parsed = urlparse(self.path)
            path = parsed.path
            if path not in ("/api/reset", "/api/backlog", "/api/switch-token",
                            "/api/set-pointer", "/api/add-token", "/api/apply-dry-run",
                            "/api/apply-armed", "/api/apply-queue-cancel"):
                auth.audit(self._client(), path, "404")
                return self._json(404, {"error": "not found"})
            if not self._authed():
                auth.audit(self._client(), path, "401")
                return self._json(401, {"error": "unauthorized"})
            # R3 apply DRY-RUN: preview making a default live (no relaunch).
            if path == "/api/apply-dry-run":
                return self._handle_apply_dry_run()
            # R4 ARMED apply: the REAL relaunch — DISABLED by default (503) unless
            # cai+Nazim flip CONSOLE_R4_ENABLED AND a live operator arm exists.
            if path == "/api/apply-armed":
                return self._handle_apply_armed()
            # Cancel a queued-on-busy apply (op#10861).
            if path == "/api/apply-queue-cancel":
                return self._handle_apply_queue_cancel()
            # R2b add-token: register a new 0600 key file so it's selectable. Raw
            # token written once, NEVER echoed/logged; only its fp is returned.
            if path == "/api/add-token":
                return self._handle_add_token()
            # R2b (op#10706): set/clear a body's token or model POINTER file — the
            # DEFAULT it boots with. Reversible (rm), NO relaunch, NO live billing
            # change. gazzabyte can never be selected (not in LANE_TOKEN_FILES);
            # model is allowlist-validated BEFORE write.
            if path == "/api/set-pointer":
                return self._handle_set_pointer()
            # Operator swipe on a "Your asks" card (op 2026-08-02): drop (left) /
            # prioritise (right). Mutates operator_backlog via the vetted
            # scripts/backlog_swipe.py — the console DB session is read-only, same
            # reason /api/reset shells out rather than writing inline.
            if path == "/api/backlog":
                return self._handle_backlog()
            if path == "/api/switch-token":
                return self._handle_switch_token()
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                body_id = (json.loads(raw or b"{}").get("body") or "").strip()
            except Exception:                                        # noqa: BLE001
                auth.audit(self._client(), path, "400")
                return self._json(400, {"error": "bad request"})
            action = RESET_ACTIONS.get(body_id)
            if not action:
                auth.audit(self._client(), f"{path}:{body_id}", "400")
                return self._json(400, {"error": "unknown body",
                                        "allowed": sorted(RESET_ACTIONS)})
            script, label = action
            auth.audit(self._client(), f"{path}:{body_id}", "run")
            logger.info("console reset requested: %s -> %s", body_id, script)
            # Idempotency claim (op#8989): atomically reject a concurrent or
            # too-soon repeat reset of the SAME body so a double-click / retried
            # POST can't double-run the destructive script.
            now = time.monotonic()
            with _RESET_GUARD_LOCK:
                if body_id in _RESET_INFLIGHT:
                    # Log the OUTCOME, not just the request (op#9262): a guard
                    # rejection used to be invisible in console.log — only auth.audit
                    # saw it — so when a reset "errored" on the operator's screen there
                    # was no way to tell a guard-reject from a crash. Now every branch
                    # logs, so the failure story is legible.
                    logger.warning("console reset REJECTED (already in progress): %s", body_id)
                    auth.audit(self._client(), f"{path}:{body_id}", "409")
                    return self._json(409, {"error": "reset already in progress",
                                            "body": body_id})
                last = _RESET_LAST_RUN.get(body_id, 0.0)
                if now - last < _RESET_COOLDOWN_S:
                    retry_after = round(_RESET_COOLDOWN_S - (now - last))
                    logger.warning("console reset REJECTED (cooldown, retry_after=%ss): %s",
                                   retry_after, body_id)
                    auth.audit(self._client(), f"{path}:{body_id}", "429")
                    return self._json(429, {"error": "reset just ran — wait before retrying",
                                            "body": body_id,
                                            "retry_after_s": retry_after})
                _RESET_INFLIGHT.add(body_id)
            try:
                r = subprocess.run(
                    ["bash", str(_REPO_ROOT / script)],
                    capture_output=True, text=True, timeout=180,
                )
                ok = r.returncode == 0
                tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-6:])
            except subprocess.TimeoutExpired:
                logger.warning("console reset TIMED OUT (180s): %s -> %s", body_id, script)
                auth.audit(self._client(), f"{path}:{body_id}", "timeout")
                return self._json(504, {"error": "reset timed out", "body": body_id})
            except Exception as e:                                   # noqa: BLE001
                logger.warning("console reset failed: %s", e)
                auth.audit(self._client(), f"{path}:{body_id}", "500")
                return self._json(500, {"error": "reset failed", "body": body_id})
            finally:
                with _RESET_GUARD_LOCK:
                    _RESET_INFLIGHT.discard(body_id)
                    _RESET_LAST_RUN[body_id] = time.monotonic()
            logger.info("console reset OUTCOME: %s -> %s (rc=%s)",
                        body_id, "ok" if ok else "script-failed", r.returncode)
            auth.audit(self._client(), f"{path}:{body_id}", "200" if ok else "500")
            return self._json(200 if ok else 500,
                              {"ok": ok, "body": body_id, "label": label, "tail": tail})

        def _handle_backlog(self):
            """POST /api/backlog {id, action} — apply an operator swipe. action is
            'drop' (swipe-left → status='dropped') or 'prioritise' (swipe-right →
            float to the top). Shells out to scripts/backlog_swipe.py so the write
            happens in the writable orchestrator env, never on the read-only
            console session (same pattern as /api/reset)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw or b"{}")
                item_id = payload.get("id")
                action = (payload.get("action") or "").strip().lower()
            except Exception:                                        # noqa: BLE001
                auth.audit(self._client(), "/api/backlog", "400")
                return self._json(400, {"error": "bad request"})
            # Validate BEFORE spawning: id must be an int, action allowlisted —
            # an unknown value never reaches the subprocess.
            if not isinstance(item_id, int) or action not in ("drop", "prioritise", "prioritize"):
                auth.audit(self._client(), f"/api/backlog:{action}", "400")
                return self._json(400, {"error": "bad id/action"})
            auth.audit(self._client(), f"/api/backlog:{action}:{item_id}", "run")
            try:
                # venv python explicitly: launchd's minimal-PATH 'python3' lacks
                # psycopg/dotenv (same reason the publishers pin an absolute tmux).
                venv_py = _REPO_ROOT / ".venv" / "bin" / "python3"
                interp = str(venv_py) if venv_py.is_file() else "python3"
                r = subprocess.run(
                    [interp, str(_REPO_ROOT / "scripts" / "backlog_swipe.py"),
                     str(item_id), action],
                    capture_output=True, text=True, timeout=30,
                )
                ok = r.returncode == 0
            except Exception as e:                                   # noqa: BLE001
                logger.warning("backlog swipe failed: %s", e)
                auth.audit(self._client(), f"/api/backlog:{action}:{item_id}", "500")
                return self._json(500, {"error": "swipe failed"})
            auth.audit(self._client(), f"/api/backlog:{action}:{item_id}", "200" if ok else "500")
            return self._json(200 if ok else 500,
                              {"ok": ok, "id": item_id, "action": action,
                               "error": None if ok else (r.stderr or "").strip()[-160:]})

        def _handle_apply_queue_cancel(self):
            """POST /api/apply-queue-cancel {session, kind} — remove a queued/held
            apply so it never fires (op#10861)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                p = json.loads(raw or b"{}")
                session = (p.get("session") or "").strip()
                kind = (p.get("kind") or "").strip().lower()
            except Exception:  # noqa: BLE001
                return self._json(400, {"error": "bad request"})
            key = f"{session}:{kind}"
            with _APPLY_QUEUE_LOCK:
                existed = _APPLY_QUEUE.pop(key, None) is not None
            auth.audit(self._client(), f"/api/apply-queue-cancel:{key}", "200" if existed else "404")
            return self._json(200, {"ok": True, "cancelled": existed, "session": session, "kind": kind})

        def _handle_apply_armed(self):
            """POST /api/apply-armed {session, kind, confirm} — the REAL armed apply.
            DISABLED/unbuilt-live: 503 unless CONSOLE_R4_ENABLED=1 (cai+Nazim gate that)
            AND a live bridge-verified operator ARM exists AND `confirm`==session (typed
            body-name guard). gazzabyte fail-closed; routes through the one switch rail
            (which verifies auth_fp + audits/alerts). I never arm it."""
            # (1) Feature flag — DISABLED by default. This alone makes R4 inert.
            if not _R4_ENABLED:
                auth.audit(self._client(), "/api/apply-armed", "503")
                return self._json(503, {"error": "R4 armed apply is DISABLED (unbuilt-live) — pending cai sign-off + Nazim review"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                p = json.loads(raw or b"{}")
                session = (p.get("session") or "").strip()
                kind = (p.get("kind") or "").strip().lower()
                confirm = (p.get("confirm") or "").strip()
            except Exception:  # noqa: BLE001
                auth.audit(self._client(), "/api/apply-armed", "400")
                return self._json(400, {"error": "bad request"})
            if not _SESSION_RE.match(session) or kind not in ("token", "model"):
                return self._json(400, {"error": "bad session/kind"})
            if _is_remote_body(session):
                return self._json(400, {"error": "remote body — armed apply runs on the VPS (cross-host, later)"})
            # (2) Live bridge-verified operator ARM (scoped + time-bounded).
            arm = _r4_current_arm()
            if not arm:
                auth.audit(self._client(), f"/api/apply-armed:{session}:no-arm", "403")
                return self._json(403, {"error": "no live operator ARM — the operator must arm via the Telegram bridge (scoped, time-bounded)"})
            # (3) Typed body-name confirm (fat-finger guard, not authority).
            if confirm != session:
                auth.audit(self._client(), f"/api/apply-armed:{session}:confirm-miss", "400")
                return self._json(400, {"error": "type the exact body name to confirm"})
            # Resolve the switch command + scope (SHARED with the queued fire path,
            # so an immediate and a deferred apply enforce identical rules).
            cmd, _account, err = _resolve_armed_apply(session, kind, arm)
            if err or cmd is None:
                code = 403 if (err and "scope" in err) else 400
                auth.audit(self._client(), f"/api/apply-armed:{kind}:{session}", str(code))
                return self._json(code, {"error": err or "cannot apply"})
            # QUEUE-ON-BUSY (op#10861): a busy lane is QUEUED, not refused — the
            # watcher fires it the moment the lane goes idle, RE-VALIDATING the arm at
            # fire time (never fires on an expired/out-of-scope arm). Never --force.
            if _lane_busy(session):
                key = f"{session}:{kind}"
                with _APPLY_QUEUE_LOCK:
                    _APPLY_QUEUE[key] = {"session": session, "kind": kind,
                                         "actor": self._client() or "operator",
                                         "status": "queued",
                                         "note": "waiting for lane to go idle"}
                auth.audit(self._client(), f"/api/apply-armed:{kind}:{session}", "202-queued")
                return self._json(202, {"queued": True, "session": session, "kind": kind,
                                        "note": "lane busy — apply QUEUED; fires when idle (arm re-checked at fire time). Cancellable."})
            # Per-lane cooldown/inflight guard (cai CAI-RESP-748 (b)) — a double-tap
            # can't fire two relaunches of the SAME body; rapid re-fire gets 429.
            now_t = time.monotonic()
            with _RESET_GUARD_LOCK:
                if session in _SWITCH_INFLIGHT:
                    auth.audit(self._client(), f"/api/apply-armed:{session}", "409")
                    return self._json(409, {"error": "apply already in progress", "session": session})
                last = _SWITCH_LAST_RUN.get(session, 0.0)
                if now_t - last < _SWITCH_COOLDOWN_S:
                    retry_after = round(_SWITCH_COOLDOWN_S - (now_t - last))
                    auth.audit(self._client(), f"/api/apply-armed:{session}", "429")
                    return self._json(429, {"error": "apply just ran — wait before retrying",
                                            "session": session, "retry_after_s": retry_after})
                _SWITCH_INFLIGHT.add(session)
            # Execute the REAL apply via the one rail. ARMED marker -> the script
            # emits the identity-stamped audit + alerts on an fp mismatch (invariants 2/3).
            auth.audit(self._client(), f"/api/apply-armed:{kind}:{session}", "run")
            try:
                _env = {**os.environ, "ARMED": "1", "BREAK_GLASS": "0",
                        "ACTOR": self._client() or "operator"}
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=_env)
                ok = r.returncode == 0
                tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-10:])
            except Exception as e:  # noqa: BLE001
                logger.warning("apply-armed failed (%s %s): %s", kind, session, e)
                auth.audit(self._client(), f"/api/apply-armed:{kind}:{session}", "500")
                return self._json(500, {"error": "apply failed"})
            finally:
                with _RESET_GUARD_LOCK:
                    _SWITCH_INFLIGHT.discard(session)
                    _SWITCH_LAST_RUN[session] = time.monotonic()
            auth.audit(self._client(), f"/api/apply-armed:{kind}:{session}", "200" if ok else "500")
            return self._json(200 if ok else 500, {"ok": ok, "kind": kind, "session": session, "output": tail})

        def _handle_apply_dry_run(self):
            """POST /api/apply-dry-run {session, kind:token|model} — PREVIEW making a
            body's configured default LIVE, by running the switch script in --dry-run.
            Changes NOTHING (no kill, no relaunch); returns the plan incl. the resume
            id (op#10706 R3). The armed apply (R4) is separate + cai-gated."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                p = json.loads(raw or b"{}")
                session = (p.get("session") or "").strip()
                kind = (p.get("kind") or "").strip().lower()
            except Exception:  # noqa: BLE001
                auth.audit(self._client(), "/api/apply-dry-run", "400")
                return self._json(400, {"error": "bad request"})
            if not _SESSION_RE.match(session) or kind not in ("token", "model"):
                auth.audit(self._client(), "/api/apply-dry-run", "400")
                return self._json(400, {"error": "bad session/kind"})
            if _is_remote_body(session):
                auth.audit(self._client(), f"/api/apply-dry-run:{session}:remote", "400")
                return self._json(400, {"error": "remote body — apply runs on the VPS host (cross-host apply is R3/R4)"})
            # Resolve the executor + args for a --dry-run (no effect).
            SW_LANE = str(_REPO_ROOT / "scripts" / "switch_lane_token.sh")
            SW_SINGLE = str(_REPO_ROOT / "scripts" / "switch_singleton_token.sh")
            if kind == "token":
                ptr = _token_pointer_name(session)
                if ptr is None:
                    auth.audit(self._client(), f"/api/apply-dry-run:token:{session}", "400")
                    return self._json(400, {"error": "token not settable for this body"})
                target = _read_pointer_target(ptr)
                if not target or not _fp_of_token_file(target):
                    auth.audit(self._client(), f"/api/apply-dry-run:token:{session}", "400")
                    return self._json(400, {"error": "no token default set to apply — pick a Token first"})
                if session == "nazim":
                    cmd = ["bash", SW_SINGLE, "--dry-run", "nazim", target]
                else:
                    cmd = ["bash", SW_LANE, "--dry-run", session, target]
            else:  # model
                if session in _MODEL_ENV_BODIES:
                    auth.audit(self._client(), f"/api/apply-dry-run:model:{session}", "400")
                    return self._json(400, {"error": "model not pointer-settable for this body"})
                cur = _current_token_file_for(session)
                if cur is None:
                    auth.audit(self._client(), f"/api/apply-dry-run:model:{session}", "400")
                    return self._json(400, {"error": "could not resolve the body's current account for a model relaunch"})
                cmd = ["bash", SW_LANE, "--dry-run", "--model-apply", session, str(cur)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                preview = ((r.stdout or "") + (r.stderr or "")).strip()
            except Exception as e:  # noqa: BLE001
                logger.warning("apply-dry-run failed (%s %s): %s", kind, session, e)
                auth.audit(self._client(), f"/api/apply-dry-run:{kind}:{session}", "500")
                return self._json(500, {"error": "dry-run failed"})
            auth.audit(self._client(), f"/api/apply-dry-run:{kind}:{session}", "200")
            return self._json(200, {"ok": True, "kind": kind, "session": session,
                                    "dry_run": True, "preview": preview})

        def _handle_add_token(self):
            """POST /api/add-token {name, token} — register a NEW 0600 token key file
            in ~/.wingmen/keys so it becomes selectable (op#10706). The RAW token is
            written ONCE (0600, O_EXCL — no clobber) and is NEVER echoed or logged;
            only its fingerprint is returned (the operator wants to SEE the fp).
            gazzabyte name/basename fail-closed (CAI-729)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                p = json.loads(raw or b"{}")
                name = (p.get("name") or "").strip().lower()
                tok = (p.get("token") or "").strip()
            except Exception:  # noqa: BLE001
                auth.audit(self._client(), "/api/add-token", "400")
                return self._json(400, {"error": "bad request"})
            if not _TOKEN_NAME_RE.match(name) or name == "gazzabyte":
                auth.audit(self._client(), "/api/add-token", "400")
                return self._json(400, {"error": "bad name (a-z 0-9 -, and not 'gazzabyte')"})
            fname = name + _TOKEN_FILE_SUFFIX
            if fname in _FORBIDDEN_TOKEN_BASENAMES:
                auth.audit(self._client(), f"/api/add-token:{name}", "400")
                return self._json(400, {"error": "forbidden token name"})
            if not tok or len(tok) < 20:            # presence/length only — never log the value
                auth.audit(self._client(), f"/api/add-token:{name}", "400")
                return self._json(400, {"error": "missing or too-short token"})
            dest = _KEYS_DIR / fname
            try:
                _KEYS_DIR.mkdir(parents=True, exist_ok=True)
                # 0600 BEFORE the secret; O_EXCL so 'add' never clobbers an existing token.
                fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(tok + "\n")
                os.chmod(dest, 0o600)
            except FileExistsError:
                auth.audit(self._client(), f"/api/add-token:{name}", "409")
                return self._json(409, {"error": "a token with that name already exists"})
            except Exception as e:  # noqa: BLE001
                logger.warning("add-token failed for %s: %s", name, e)   # name only, never the token
                auth.audit(self._client(), f"/api/add-token:{name}", "500")
                return self._json(500, {"error": "write failed"})
            fp = _fp_of_token_file(str(dest))                            # fp is non-secret
            auth.audit(self._client(), f"/api/add-token:{name}:{fp}", "200")
            logger.info("add-token: registered %s (fp %s)", name, fp)
            return self._json(200, {"ok": True, "name": name, "fp": fp})

        def _handle_set_pointer(self):
            """POST /api/set-pointer {kind:"token"|"model", session, value?|clear?}
            Set/clear a body's token or model POINTER (the default it boots with).
            REVERSIBLE (clear = rm), NO relaunch, NO live billing change. gazzabyte
            can never be selected (not in LANE_TOKEN_FILES); model is allowlist-
            validated BEFORE write; session is charset-restricted (no path escape).
            Audit sink = auth.audit (the console DB session is read-only)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                p = json.loads(raw or b"{}")
                kind = (p.get("kind") or "").strip().lower()
                session = (p.get("session") or "").strip()
                clear = bool(p.get("clear"))
                value = (p.get("value") or "").strip()
            except Exception:  # noqa: BLE001
                auth.audit(self._client(), "/api/set-pointer", "400")
                return self._json(400, {"error": "bad request"})
            if not _SESSION_RE.match(session):
                auth.audit(self._client(), "/api/set-pointer", "400")
                return self._json(400, {"error": "bad session"})
            if kind not in ("token", "model"):
                auth.audit(self._client(), "/api/set-pointer", "400")
                return self._json(400, {"error": "bad kind"})
            # FAIL LOUD, never a silent no-op (fix (a)): refuse a write whose pointer
            # this host's boot does not honor — a remote (VPS) body, or a model on a
            # body whose model is env-driven at boot (not .<session>_model).
            if _is_remote_body(session):
                auth.audit(self._client(), f"/api/set-pointer:{kind}:{session}:remote", "400")
                return self._json(400, {"error": "remote body — its default lives on the VPS host, not here; cross-host apply lands in R3/R4"})
            if kind == "model" and session in _MODEL_ENV_BODIES:
                auth.audit(self._client(), f"/api/set-pointer:model:{session}:env", "400")
                return self._json(400, {"error": "model for this body is env-driven at boot (NAZIM_MODEL/CAI_MODEL/…), not a pointer — no-op if written"})
            try:
                if kind == "token":
                    ptr = _token_pointer_name(session)
                    if ptr is None:
                        auth.audit(self._client(), f"/api/set-pointer:token:{session}", "400")
                        return self._json(400, {"error": "token not settable for this body (boots off .env)"})
                    pfile = _REPO_ROOT / ptr
                    if clear:
                        if pfile.exists():
                            pfile.unlink()
                        auth.audit(self._client(), f"/api/set-pointer:token:{session}:clear", "200")
                        logger.info("set-pointer token CLEAR: %s (%s)", session, ptr)
                    else:
                        tname = value.lower()
                        tokfile = _resolve_registry_token(tname)
                        if tokfile is None or tokfile.name in _FORBIDDEN_TOKEN_BASENAMES:
                            auth.audit(self._client(), f"/api/set-pointer:token:{session}", "400")
                            return self._json(400, {"error": "unknown/unavailable token",
                                                    "allowed": [e["name"] for e in _token_registry()]})
                        pfile.write_text(str(tokfile) + "\n", encoding="utf-8")
                        auth.audit(self._client(), f"/api/set-pointer:token:{session}:{tname}", "200")
                        logger.info("set-pointer token: %s -> %s (%s)", session, tname, ptr)
                else:  # model
                    mfile = _REPO_ROOT / (".%s_model" % session)
                    if clear:
                        if mfile.exists():
                            mfile.unlink()
                        auth.audit(self._client(), f"/api/set-pointer:model:{session}:clear", "200")
                        logger.info("set-pointer model CLEAR: %s", session)
                    else:
                        if value not in _MODEL_ALLOWLIST:
                            auth.audit(self._client(), f"/api/set-pointer:model:{session}", "400")
                            return self._json(400, {"error": "model not allowlisted", "allowed": list(_MODEL_ALLOWLIST)})
                        mfile.write_text(value + "\n", encoding="utf-8")
                        auth.audit(self._client(), f"/api/set-pointer:model:{session}:{value}", "200")
                        logger.info("set-pointer model: %s -> %s", session, value)
            except Exception as e:  # noqa: BLE001
                logger.warning("set-pointer failed: %s", e)
                auth.audit(self._client(), f"/api/set-pointer:{kind}:{session}", "500")
                return self._json(500, {"error": "write failed"})
            return self._json(200, {
                "ok": True, "kind": kind, "session": session, "cleared": clear,
                "note": "default set — applies on the body's NEXT relaunch (no relaunch now)",
            })

        def _handle_switch_token(self):
            """POST /api/switch-token {lane, token_name} — re-token a lane onto a
            different terms-clean Claude Max account. Kills + relaunches the lane's
            tmux session on the new account via scripts/switch_lane_token.sh (the
            only way to change a lane's account — it is fixed at process launch).

            Same auth as /api/reset (checked in do_POST before dispatch). The
            token_name is mapped to a key file via the LANE_TOKEN_FILES allowlist,
            which EXCLUDES the forbidden gazzabyte consumer token (CAI-729); the
            script re-checks the basename independently. lane is validated against a
            strict charset (it is passed as argv, never a shell string, so this is
            defense in depth, not the only barrier)."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw or b"{}")
                lane = (payload.get("lane") or "").strip()
                token_name = (payload.get("token_name") or "").strip().lower()
            except Exception:                                        # noqa: BLE001
                auth.audit(self._client(), "/api/switch-token", "400")
                return self._json(400, {"error": "bad request"})
            # Validate BEFORE resolving/spawning: strict session charset + an
            # allowlisted token name whose key file resolves + is readable.
            if not _LANE_SESSION_RE.match(lane):
                auth.audit(self._client(), f"/api/switch-token:{token_name}", "400")
                return self._json(400, {"error": "bad lane"})
            if token_name not in LANE_TOKEN_FILES:
                auth.audit(self._client(), f"/api/switch-token:{token_name}", "400")
                return self._json(400, {"error": "unknown token",
                                        "allowed": sorted(LANE_TOKEN_FILES)})
            tokfile = _resolve_lane_token_file(token_name)
            if tokfile is None:
                auth.audit(self._client(), f"/api/switch-token:{token_name}", "400")
                return self._json(400, {"error": "token key file not available",
                                        "token_name": token_name})
            key = f"{lane}:{token_name}"
            auth.audit(self._client(), f"/api/switch-token:{key}", "run")
            logger.info("console switch-token requested: lane=%s -> %s", lane, token_name)
            # Idempotency claim (parity with /api/reset): reject a concurrent or
            # too-soon repeat switch of the SAME lane so a double-click can't fire
            # two kills.
            now = time.monotonic()
            with _RESET_GUARD_LOCK:
                if lane in _SWITCH_INFLIGHT:
                    logger.warning("console switch-token REJECTED (in progress): %s", lane)
                    auth.audit(self._client(), f"/api/switch-token:{key}", "409")
                    return self._json(409, {"error": "switch already in progress", "lane": lane})
                last = _SWITCH_LAST_RUN.get(lane, 0.0)
                if now - last < _SWITCH_COOLDOWN_S:
                    retry_after = round(_SWITCH_COOLDOWN_S - (now - last))
                    logger.warning("console switch-token REJECTED (cooldown %ss): %s", retry_after, lane)
                    auth.audit(self._client(), f"/api/switch-token:{key}", "429")
                    return self._json(429, {"error": "switch just ran — wait before retrying",
                                            "lane": lane, "retry_after_s": retry_after})
                _SWITCH_INFLIGHT.add(lane)
            try:
                # BREAK-GLASS marker + actor (cai CAI-RESP-747 conds 2/3): this is
                # the emergency re-token path, so the script emits a P1 break-glass
                # alert + enriched audit row (who/lane/from->to fp) on completion.
                _env = {**os.environ, "BREAK_GLASS": "1", "ACTOR": self._client() or "operator"}
                r = subprocess.run(
                    ["bash", str(_REPO_ROOT / "scripts" / "switch_lane_token.sh"),
                     lane, str(tokfile)],
                    capture_output=True, text=True, timeout=150, env=_env,
                )
                ok = r.returncode == 0
                tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-8:])
            except subprocess.TimeoutExpired:
                logger.warning("console switch-token TIMED OUT (150s): %s", lane)
                auth.audit(self._client(), f"/api/switch-token:{key}", "timeout")
                return self._json(504, {"error": "switch timed out", "lane": lane})
            except Exception as e:                                   # noqa: BLE001
                logger.warning("console switch-token failed: %s", e)
                auth.audit(self._client(), f"/api/switch-token:{key}", "500")
                return self._json(500, {"error": "switch failed", "lane": lane})
            finally:
                with _RESET_GUARD_LOCK:
                    _SWITCH_INFLIGHT.discard(lane)
                    _SWITCH_LAST_RUN[lane] = time.monotonic()
            logger.info("console switch-token OUTCOME: lane=%s -> %s (rc=%s)",
                        lane, token_name, r.returncode)
            auth.audit(self._client(), f"/api/switch-token:{key}", "200" if ok else "500")
            return self._json(200 if ok else 500,
                              {"ok": ok, "lane": lane, "token_name": token_name, "tail": tail})

        def _serve_static(self, name: str, path: str, cache_control: str = "no-cache") -> None:
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
    # Pre-warm the DB connection pool off the main thread so the operator's
    # first /api/fleet after a restart is already warm (~150ms), not a
    # ~650ms×N cold connect. Non-blocking + fail-soft: a DB blip at boot just
    # means the pool fills lazily on first request instead.
    threading.Thread(target=db.warm_pool, kwargs={"n": 3}, daemon=True).start()
    # Queue-on-busy apply watcher (op#10861): fires queued armed applies when their
    # lane goes idle, re-validating the R4 arm at fire time. Daemon; a no-op while
    # the queue is empty (which it always is unless R4 is enabled + something queued).
    threading.Thread(target=_apply_queue_watcher, daemon=True).start()
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
