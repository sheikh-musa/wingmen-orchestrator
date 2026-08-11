#!/usr/bin/env python3
"""body_activity_oracle — the pane-truth "what is this body doing RIGHT NOW?"
primitive (op#11774 Phase 0, console-signed 18644).

WHY: the fleet treated two signals as truth that aren't — "read == handled" and
"heartbeat == alive". Both failed on 2026-08-11 (the hub READ two P1 routes then
parked 4h; its heartbeat was 17h stale WHILE working). Every durable wedge/wake
fix needs the SAME missing primitive: a fresh, confidence-tagged verdict read from
the body's actual tmux pane, not from a flag that lies. This module is that
primitive. It REUSES scripts/lib/composer_capture.sh (CC_BUSY/CC_EMPTY/CC_GHOST/
CC_PARTIAL/CC_UNSURE/CC_N) — the same battle-tested capture the reset scripts use.

VERDICTS: WORKING | IDLE_EMPTY | STAGED | GHOST_WEDGED | UNSURE.

GUARDRAILS (all enforced here):
  * fail-CLOSED: ANY busy evidence -> WORKING, even under a partial capture. We
    never report a maybe-working body as idle — a false IDLE is the one that
    destroys work / interrupts a turn.
  * UNSURE -> do nothing, never guess: capture failure, low-confidence capture, an
    unreachable remote host (the cross-host hub) all resolve to UNSURE. Consumers
    act ONLY on a high-confidence verdict.
  * DETECT-ONLY in Phase 0: this module READS. The mutating probe (fork-2, cai-
    gated) is BUILT-BUT-INERT — `PROBE_ARMED` ships False; disarmed it returns
    UNSURE and NEVER sends a keystroke to a live pane.

Phase 1 will add the consumers (wake self-drive #2, escalate-on-overdue #5, honest
board #4a, auto-recycle gate #3) on top of this verdict — each with its own
per-stage sign, lease-gating (req A), and attributable verified-submit (req B).
"""
from __future__ import annotations

import os
import subprocess
from collections import namedtuple

# ── verdict vocabulary ──────────────────────────────────────────────────────
WORKING = "WORKING"          # mid-turn ('esc to interrupt' / waiting on bg agents)
IDLE_EMPTY = "IDLE_EMPTY"    # at an empty composer, nothing staged
STAGED = "STAGED"            # real text sitting unsubmitted
GHOST_WEDGED = "GHOST_WEDGED"  # a history/ghost autosuggestion occupying the composer
UNSURE = "UNSURE"            # cannot tell — fail safe, do nothing, alert

Verdict = namedtuple("Verdict", "state reason")

LOCAL_HOST = "_local_"

# The mutating probe (fork-2) is DISARMED until cai signs. Flipping this True
# without the cai safety sign is a boundary violation.
PROBE_ARMED = False

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMPOSER_LIB = os.path.join(_HERE, "..", "scripts", "lib", "composer_capture.sh")

# Freshness gate for a DB-published (cross-host) verdict: older than this -> UNSURE,
# so a dead VPS publisher fails SAFE rather than serving a stale coverage-guess.
VERDICT_TTL_S = int(os.environ.get("ORACLE_VERDICT_TTL_S", "180"))


class RemoteUnreachable(Exception):
    """Raised by a capture backend when a cross-host body (the VPS hub) cannot be
    reached — resolves to UNSURE, never a guessed verdict."""


# ── the pure heart: signals -> verdict ──────────────────────────────────────
def classify(signals) -> Verdict:
    """Map composer-capture signals to a verdict. PURE + total (always returns a
    Verdict). Ordering encodes the guardrails:
      1. no/failed capture           -> UNSURE
      2. busy (even if partial)      -> WORKING   (fail-closed: activity wins)
      3. low-confidence capture      -> UNSURE    (can't trust empty/staged/ghost)
      4. ghost                       -> GHOST_WEDGED
      5. empty                       -> IDLE_EMPTY
      6. has content                 -> STAGED
    """
    if not signals or not signals.get("capture_ok"):
        return Verdict(UNSURE, "capture-failed")
    if signals.get("busy"):
        return Verdict(WORKING, "busy-marker")
    if not signals.get("partial_ok") or signals.get("unsure"):
        return Verdict(UNSURE, "low-confidence-capture")
    if signals.get("ghost"):
        return Verdict(GHOST_WEDGED, "history-ghost")
    if signals.get("empty"):
        return Verdict(IDLE_EMPTY, "empty-composer")
    if (signals.get("n") or 0) > 0:
        return Verdict(STAGED, "staged-text")
    return Verdict(UNSURE, "indeterminate")


def signals_from_cc(env: dict) -> dict:
    """Normalize raw CC_* strings (from the composer_capture shell-out) into the
    dict `classify` consumes. A present env means the capture ran (capture_ok);
    a missing/failed shell-out is represented by passing None to classify."""
    def flag(key, default="0"):
        return str(env.get(key, default)).strip() == "1"
    return {
        "capture_ok": True,
        # busy OR busy-stale (a frozen busy render) both mean "do not touch".
        "busy": flag("CC_BUSY") or flag("CC_BUSY_STALE"),
        "partial_ok": str(env.get("CC_PARTIAL", "noborder")).strip() == "ok",
        "unsure": flag("CC_UNSURE"),
        "ghost": flag("CC_GHOST"),
        "empty": flag("CC_EMPTY"),
        "n": int(str(env.get("CC_N", "0")).strip() or "0"),
    }


# ── orchestration: resolve host -> capture -> classify ──────────────────────
def activity(agent_id: str, *, capture=None, resolve_host=None,
             resolve_session=None, read_verdict=None) -> Verdict:
    """The verdict for `agent_id`. Seams (`capture`, `resolve_host`,
    `resolve_session`, `read_verdict`) are injectable so the logic is unit-testable
    without tmux/DB; defaults wire the real backends. Any failure -> UNSURE.

    LOCAL host  -> capture the pane + classify (the pane-truth path).
    REMOTE host -> read the DB-published verdict (G-b: the VPS-instance oracle reads
      the hub's LOCAL pane and publishes here). Fresh -> that verdict; stale/missing/
      error -> UNSURE (fail-safe: a dead publisher never yields a stale coverage-
      guess, and #2 falls back to #5 escalate-a-human). NO ssh in this read path."""
    resolve_host = resolve_host or _default_resolve_host
    host = resolve_host(agent_id)

    if host != LOCAL_HOST:
        read_verdict = read_verdict or _default_read_verdict
        try:
            v = read_verdict(agent_id)
        except Exception as e:  # DB error -> UNSURE, never a guess
            return Verdict(UNSURE, f"verdict-read-error:{type(e).__name__}")
        return v if v is not None else Verdict(UNSURE, "remote-verdict-stale-or-missing")

    resolve_session = resolve_session or _default_resolve_session
    capture = capture or _default_capture
    session = resolve_session(agent_id)
    if not session:
        return Verdict(UNSURE, "no-session")
    try:
        signals = capture(host, session)
    except RemoteUnreachable:
        return Verdict(UNSURE, "remote-unreachable")
    except Exception as e:  # any capture error -> UNSURE, never a guess
        return Verdict(UNSURE, f"capture-error:{type(e).__name__}")
    if signals is None:
        return Verdict(UNSURE, "capture-none")
    return classify(signals)


# ── the mutating probe: BUILT-BUT-INERT until fork-2 (cai safety sign) ───────
def probe_confirm_empty(session: str, *, armed: bool = None,
                        sendkeys=None, tmux_bin: str = None) -> Verdict:
    """Disambiguate the invisible-submit ghost class that the non-mutating signals
    cannot (the class that needed reset_fleet_health --RESET_FORCE, op#18548): type
    a self-undoing sentinel and see whether it REPLACES ghost text (ghost) or
    APPENDS to real text (staged).

    INERT until fork-2: with `armed` False (the default, `PROBE_ARMED`), this returns
    UNSURE and sends NOTHING to the pane. When cai signs fork-2 the armed path runs
    under HARD invariants (req A/B): SRE-lease-gated caller, self-undoing type-then-
    delete only, fail-CLOSED (only when the oracle is already high-confidence
    NOT-WORKING; any ambiguity -> abort + leave the pane untouched + alert)."""
    armed = PROBE_ARMED if armed is None else armed
    if not armed:
        # DO NOT touch the pane. This is the whole point of built-but-inert.
        return Verdict(UNSURE, "probe-disarmed (fork-2 not signed)")
    # --- armed path (runs only after cai fork-2 sign + a live-tmux proof) ---
    # Enforced-in-code invariants live at the CALLER (Phase 1): lease-gate (req A)
    # + attributable verified-submit for any real re-drive (req B). The probe here
    # only ever types a transient self-undoing sentinel, never persistent text.
    raise NotImplementedError(
        "armed mutating probe is pending fork-2 (cai) sign + live-tmux TDD")


# ── default real backends (not unit-tested here — need live tmux/ssh) ────────
def _default_resolve_session(agent_id: str):
    """agent_id -> its tmux session name. Thin wrapper over the shared resolver."""
    try:
        from agent_wake import resolve_tmux_session  # same-dir module at runtime
        return resolve_tmux_session(agent_id)
    except Exception:
        return None


def _default_resolve_host(agent_id: str) -> str:
    """Which host the body lives on. The hub (cc-orchestrator) is on the VPS;
    everything else is local to this instance (Mini). Mirrors the wake sweep's
    one-instance-per-host split."""
    return "vps" if agent_id in ("cc-orchestrator", "cc-infra") else LOCAL_HOST


def _default_capture(host: str, session: str):
    """Capture the composer_capture CC_* signals for `session`. Local: source the
    shared shell lib and read the pane. Remote (VPS hub): cross-host reach is
    Phase-0-pending — until wired it raises RemoteUnreachable so the hub verdict
    fails safe to UNSURE rather than a guess."""
    if host != LOCAL_HOST:
        # G-b cross-host hub reach: to be wired as a VPS-side oracle instance (the
        # same split the wake sweep uses) or an explicit ssh read. Until then,
        # fail SAFE — never fabricate the hub's state.
        raise RemoteUnreachable(host)
    return _capture_local(session)


def _dsn():
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _default_read_verdict(agent_id: str):
    """Read the DB-published verdict for a remote body (the VPS hub). Returns a
    Verdict if fresh (updated_at within VERDICT_TTL_S), else None (stale/missing ->
    UNSURE upstream). Raises on DB error (-> UNSURE upstream); never fabricates."""
    import psycopg
    with psycopg.connect(_dsn(), connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT state, reason FROM public.body_activity_verdict "
            "WHERE agent = %s AND updated_at > now() - make_interval(secs => %s)",
            (agent_id, VERDICT_TTL_S))
        row = cur.fetchone()
    if not row:
        return None
    return Verdict(row[0], row[1] or "")


def publish_verdict(agent_id: str, verdict: Verdict, host: str) -> None:
    """UPSERT a verdict to the shared-substrate cache (used by the VPS-instance
    publisher). Raises on error so the caller fails LOUD; never swallows."""
    import psycopg
    with psycopg.connect(_dsn(), connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.body_activity_verdict (agent, state, reason, host, updated_at) "
            "VALUES (%s, %s, %s, %s, now()) "
            "ON CONFLICT (agent) DO UPDATE SET state=EXCLUDED.state, reason=EXCLUDED.reason, "
            "  host=EXCLUDED.host, updated_at=now()",
            (agent_id, verdict.state, verdict.reason, host))
        conn.commit()


def _capture_local(session: str):
    """Source composer_capture.sh, run pane_busy + composer_parse_pane against the
    live pane, and echo the CC_* signals for parsing. Returns None on any failure
    (-> UNSURE upstream)."""
    tmux_bin = os.environ.get("TMUX_BIN", "/usr/local/bin/tmux")
    pane = f"={session}:0.0"
    script = (
        f'source "{_COMPOSER_LIB}" || exit 3\n'
        f'pane_busy "{tmux_bin}" "{pane}" 2>/dev/null || true\n'
        f'composer_parse_pane "{tmux_bin}" "{pane}" 2>/dev/null || exit 4\n'
        'echo "CC_BUSY=${CC_BUSY:-0}"\n'
        'echo "CC_BUSY_STALE=${CC_BUSY_STALE:-0}"\n'
        'echo "CC_EMPTY=${CC_EMPTY:-0}"\n'
        'echo "CC_GHOST=${CC_GHOST:-0}"\n'
        'echo "CC_PARTIAL=${CC_PARTIAL:-noborder}"\n'
        'echo "CC_UNSURE=${CC_UNSURE:-1}"\n'
        'echo "CC_N=${CC_N:-0}"\n'
    )
    try:
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    env = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    if not env:
        return None
    return signals_from_cc(env)
