#!/usr/bin/env python3
"""irsyad_shadow_watch.py — wake cc-irsyad the moment a real client message lands.

WHY: the `gazzabyte-irsyad` channel is polled by the HUB's ingest, and that ingest nudges
the hub. cc-irsyad is on the Mini and is deliberately NOT on that channel yet (the hub still
owns the live thread), so without this it would only notice a client message on its next turn
— which makes "is the dedicated agent faster than the hub?" unanswerable and its drafts late.

WHAT IT DOES: polls the durable log for new inbound rows on the client tag and nudges the
`irsyad` tmux session (count-only — never payload, CAI-RESP-357). It sends nothing to the
client and touches no hub state; it is a shadow, running alongside the hub, not instead of it.
Both responders are then timed off the same durable log — see irsyad_latency_report.py.

DEAD-MAN'S SWITCH: a monitor that fails silently is worse than none. Every loop stamps a
heartbeat file; on boot, a heartbeat older than STALE_AFTER means we were dead through a
window and the gap is reported to Nazim on the bus. Any unhandled exception files a P1 bus
row before exiting non-zero so launchd's KeepAlive restart is visible, not silent.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.lib import fire_window  # noqa: E402  (quiesce during a recycle fire window)
from datetime import datetime, timedelta, timezone

import psycopg

# Cross-host media mirror (pulls hub-downloaded client attachments onto the Mini so
# cc-irsyad-coord can Read them — see reports/media-download-fix-v2.md). Imported
# defensively: this is the client-WAKE path, so a broken/absent mirror helper must
# NEVER stop the watcher from booting or nudging.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import irsyad_media_mirror as _mirror  # noqa: E402
    MIRROR_OK = True
except Exception as _mirror_exc:  # noqa: BLE001
    _mirror = None
    MIRROR_OK = False

ORCH = pathlib.Path(os.environ.get("ORCH_DIR", pathlib.Path.home() / "wingmen/orchestrator"))
CLIENT_TAG = "gazzabyte-irsyad"
LANE_SESSION = "irsyad"
POLL_SEC = int(os.environ.get("IRSYAD_SHADOW_POLL_SEC", "20"))
STALE_AFTER = timedelta(seconds=POLL_SEC * 15)
STATE = ORCH / "logs/.irsyad_shadow_offset"
HEARTBEAT = ORCH / "logs/.irsyad_shadow_heartbeat"
LOG = ORCH / "logs/irsyad_shadow_watch.log"


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.exit("DATABASE_URL not set")
    return dsn


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def bus(subject: str, body: str, priority: str = "P2") -> None:
    """Tell Nazim. Best-effort: a broken bus must not take the watcher down."""
    try:
        with psycopg.connect(_dsn(), connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, "
                "body, priority) VALUES ('substrate','orch-console','update',%s,%s,%s)",
                (subject, body, priority))
            conn.commit()
    except Exception as exc:                      # noqa: BLE001 — never fatal
        log(f"bus write failed: {exc}")


def read_offset() -> int:
    try:
        return int(STATE.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_offset(value: int) -> None:
    STATE.write_text(str(value))


def check_deadman() -> None:
    """Boot-time gap check — did we miss a window while dead?"""
    try:
        last = datetime.fromisoformat(HEARTBEAT.read_text().strip())
    except (OSError, ValueError):
        log("no prior heartbeat (first boot)")
        return
    gap = datetime.now(timezone.utc) - last
    if gap > STALE_AFTER:
        log(f"DEGRADED: heartbeat gap {gap} — the shadow was dead through that window")
        bus("irsyad shadow-watcher was DOWN — client messages may not have woken cc-irsyad",
            f"The shadow watcher's heartbeat was stale by {gap} (threshold {STALE_AFTER}).\n"
            f"During that window cc-irsyad would NOT have been woken by new client messages "
            f"on '{CLIENT_TAG}'. The hub was unaffected — it polls that channel itself, so no "
            f"client message was missed by the fleet; only cc-irsyad's shadow drafting and the "
            f"latency comparison have a hole. Check logs/irsyad_shadow_watch.log.", "P1")


def new_inbound(conn, since_id: int) -> tuple[int, int]:
    """(count of new inbound client rows, max id)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), coalesce(max(id), %s) FROM operator_messages "
            "WHERE direction='inbound' AND tag=%s AND id > %s",
            (since_id, CLIENT_TAG, since_id))
        return cur.fetchone()


def unhandled_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM operator_messages WHERE direction='inbound' AND tag=%s "
            "AND handled_at IS NULL", (CLIENT_TAG,))
        return cur.fetchone()[0]


# ── INTERIM client-facing ghost-bypass auto-guard (op#18375) ──────────────────
# lane_nudge REFUSES (rc=3) when the composer "holds unsent text" — but that text
# is often a history-AUTOSUGGESTION GHOST (idle lane, entirely-dim) that BLOCKS the
# client wake while the lane is actually idle+empty. This guard bypasses the block
# ONLY for a probe-confirmed ghost on THIS client lane, so client Q&A stops waiting
# on manual hand-nudges. It does NOT touch the shared lane_nudge/composer_capture
# classifier the reset kill-switches consume — that clean fix (entirely-dim+idle in
# composer_capture) is under separate cross-review and SUPERSEDES this scrappy guard.
# Triple-gated so it can NEVER clobber real staged work: (1) idle, (2) entirely-dim,
# (3) a definitive self-reversing PROBE (type sentinel -> a ghost is REPLACED by it;
# real text is APPENDED). Only on all three do we clear+submit.
TMUX_BIN = next((p for p in ("/usr/local/bin/tmux", "/opt/homebrew/bin/tmux", "/usr/bin/tmux")
                 if pathlib.Path(p).exists()), "tmux")
GHOST_SENTINEL = "µ"  # µ — rare; unlikely to collide with staged content


def _cc(session: str) -> "tuple[str, str, str]":
    """(CC_EMPTY, CC_PH_BASIS, CC_FLAT) via the shared composer_capture lib (read-only)."""
    try:
        r = subprocess.run(
            ["bash", "-c",
             f'. "{ORCH}/scripts/lib/composer_capture.sh"; '
             f'composer_parse_pane "{TMUX_BIN}" "{session}"; '
             f'printf "%s\\t%s\\t%s" "$CC_EMPTY" "$CC_PH_BASIS" "$CC_FLAT"'],
            capture_output=True, text=True, timeout=20)
        parts = (r.stdout or "").split("\t")
        return (parts[0], parts[1], parts[2]) if len(parts) >= 3 else ("", "", "")
    except Exception as exc:                      # noqa: BLE001
        log(f"_cc error: {exc}")
        return ("", "", "")


def _busy(session: str) -> bool:
    try:
        r = subprocess.run([TMUX_BIN, "capture-pane", "-t", session, "-p"],
                           capture_output=True, text=True, timeout=15)
        return "esc to interrupt" in "\n".join((r.stdout or "").splitlines()[-3:])
    except Exception:                             # noqa: BLE001
        return True   # fail-safe: unknown => treat as busy => do NOT bypass


def _probe_confirms_ghost(session: str) -> bool:
    """Definitive, self-reversing: type a sentinel; a GHOST is replaced by just the
    sentinel (empty underneath), REAL text has the sentinel APPENDED. Always BSpace back."""
    # A recycle owns this pane while it wipes and clears; a sentinel typed into that
    # window jams the /clear. Report "not confirmed" rather than probe — the caller
    # treats that as inconclusive and re-checks on the next pass.
    if fire_window.is_held(session):
        return False
    _, _, before = _cc(session)
    subprocess.run([TMUX_BIN, "send-keys", "-t", session, "-l", GHOST_SENTINEL], timeout=15)
    time.sleep(1.0)
    _, _, after = _cc(session)
    subprocess.run([TMUX_BIN, "send-keys", "-t", session, "BSpace"], timeout=15)
    time.sleep(0.5)
    return after.strip() == GHOST_SENTINEL and before.strip() != GHOST_SENTINEL


def _verified_submit(session: str, line: str) -> bool:
    """C-u clear -> type -> Enter -> confirm working -> extra-Enter retry (mirrors lane_nudge)."""
    for _ in range(3):
        subprocess.run([TMUX_BIN, "send-keys", "-t", session, "C-u"], timeout=15); time.sleep(0.4)
        subprocess.run([TMUX_BIN, "send-keys", "-t", session, "C-u"], timeout=15); time.sleep(0.4)
        subprocess.run([TMUX_BIN, "send-keys", "-t", session, "-l", line], timeout=15); time.sleep(1.0)
        subprocess.run([TMUX_BIN, "send-keys", "-t", session, "Enter"], timeout=15); time.sleep(4.0)
        if _busy(session):
            return True
        subprocess.run([TMUX_BIN, "send-keys", "-t", session, "Enter"], timeout=15); time.sleep(3.0)
        if _busy(session):
            return True
    return False


def ghost_bypass(session: str, line: str) -> bool:
    """Triple-gated bypass of a ghost-blocked wake. Returns True only if it actually
    submitted after confirming a ghost; False (safe, preserves) on any doubt."""
    if _busy(session):
        return False                              # gate 1: only idle
    _, ph_basis, _ = _cc(session)
    if "dim" not in ph_basis or "not-dim" in ph_basis:
        return False                              # gate 2: only entirely-dim (real is not-dim)
    if not _probe_confirms_ghost(session):        # gate 3: definitive probe
        log(f"ghost-bypass: probe says REAL staged text on '{session}' — NOT bypassing, preserved")
        return False
    ok = _verified_submit(session, line)
    log(f"ghost-bypass: probe-CONFIRMED ghost on '{session}'; verified-submit {'OK' if ok else 'FAILED'}")
    return ok


def nudge(n_new: int, n_unhandled: int) -> bool:
    """Count-only wake. Carries no message content, ever."""
    line = (f"\U0001F4E5 {n_new} new client message(s) on '{CLIENT_TAG}' "
            f"({n_unhandled} unhandled) — reconcile operator_messages and draft. "
            f"The hub still owns the send.")
    try:
        r = subprocess.run([str(ORCH / "scripts/lane_nudge.sh"), LANE_SESSION, line],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            log(f"nudge failed rc={r.returncode}: {r.stdout.strip()} {r.stderr.strip()}")
            # rc=3 = refused ("composer holds text"). If it's a probe-confirmed idle GHOST,
            # bypass the block so the client wake lands (op#18375 interim guard).
            if r.returncode == 3 and ghost_bypass(LANE_SESSION, line):
                log("nudge: ghost-bypass succeeded — client wake delivered despite the ghost")
                return True
        return r.returncode == 0
    except Exception as exc:                      # noqa: BLE001
        log(f"nudge error: {exc}")
        return False


def main() -> None:
    log(f"shadow watcher up (poll={POLL_SEC}s, tag={CLIENT_TAG}, lane={LANE_SESSION})")
    check_deadman()
    offset = read_offset()
    if offset == 0:
        # First boot: start from NOW, don't replay history into the lane.
        with psycopg.connect(_dsn(), connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(id),0) FROM operator_messages WHERE tag=%s",
                        (CLIENT_TAG,))
            offset = cur.fetchone()[0]
        write_offset(offset)
        log(f"first boot — starting from id {offset}")

    consecutive_failures = 0
    while True:
        try:
            with psycopg.connect(_dsn(), connect_timeout=15) as conn:
                n_new, max_id = new_inbound(conn, offset)
                if n_new:
                    n_unhandled = unhandled_count(conn)
                    log(f"{n_new} new client message(s) (through id {max_id}); nudging lane")
                    if nudge(n_new, n_unhandled):
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures == 3:
                            bus("cc-irsyad is not being woken — 3 consecutive nudge failures",
                                f"The shadow watcher could not wake tmux session "
                                f"'{LANE_SESSION}' three times running (client messages ARE "
                                f"arriving on '{CLIENT_TAG}'). The lane is probably down or "
                                f"wedged. The hub is unaffected. Check `tmux ls` on the Mini.",
                                "P1")
                    offset = max_id
                    write_offset(offset)
                # Cross-host media mirror — best-effort, AFTER the nudge so a slow
                # scp never delays the client wake. Own offset, self-healing; a
                # failure here is logged, never fatal (client wake must survive).
                if MIRROR_OK:
                    try:
                        _mirror.run_since_offset(conn)
                    except Exception as exc:      # noqa: BLE001 — never fatal
                        log(f"media mirror error (non-fatal): {exc}")
            HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat())
        except Exception:                          # noqa: BLE001
            log("FATAL:\n" + traceback.format_exc())
            bus("irsyad shadow-watcher CRASHED (launchd will restart it)",
                "Unhandled exception in irsyad_shadow_watch.py — see "
                "logs/irsyad_shadow_watch.log. cc-irsyad may not be woken by client "
                "messages until it is back. The hub is unaffected.", "P1")
            raise
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
