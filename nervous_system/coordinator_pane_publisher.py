#!/usr/bin/env python3
"""coordinator_pane_publisher.py — host-local tmux panes -> substrate coordinator_panes.

The zero-SSH cross-host coordinator peek (op #3729, task #13). The fleet console
peeks a lane's LIVE tmux pane locally; a coordinator/lane running OFF the console
host can't be peeked that way, and the console must NOT gain outbound SSH (attack
surface — reverted per operator #3729 / Nazim #7877). Instead each HOST runs one
of these publishers: it captures every live fleet tmux session on THIS host and
UPSERTs the raw pane into coordinator_panes(agent_id, pane_text, captured_at); the
console (which already reads the substrate) READS the row instead of an SSH peek.

Topology (Mini->Studio migration, task #21): this Studio instance publishes the
hub + all Studio lanes. Nazim's 'nazim' pane still runs on the Mac MINI — that
needs a SECOND Mini-side instance publishing agent_id='orch-console' (follow-up;
this daemon never touches that row).

Contract:
  - READ-ONLY on tmux (list-sessions + capture-pane), WRITE-ONLY on the DB
    (UPSERT, never DELETE — so a peer host's rows, e.g. the Mac Mini's
    'orch-console', are never clobbered by this instance).
  - Store RAW pane text; the console reflows/cleans on read (panes.py). We do NOT
    duplicate cleaning logic here.
  - Fail-soft: a capture that fails (session gone mid-tick) or an empty pane is
    SKIPPED, never written — a brief outage just degrades the console peek to its
    bus-activity feed (it treats a snapshot older than ~90s as stale).

Identity (session -> agent_id): resolved the SAME way the console identifies a
lane card — via agent_status.tmux_session, the per-instance session name each live
agent self-registers at boot (migration 005), mapped to agent_status.agent_id (the
per-instance sub-tag, e.g. 'cc-cosem-platform-1'). Zero hardcoded map: if the
fleet changes, the next tick picks it up. The HUB ('orch' session) does not
self-register a tmux_session, so it is special-cased to 'cc-orchestrator' — the id
the console's coordinator card uses (console/db.py build_coordinators_query).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Dict, List, Optional

import psycopg
from dotenv import load_dotenv


# ── Config ────────────────────────────────────────────────────────────────────
INTERVAL_S = 10             # cadence; console treats a >90s-old snapshot as stale
STALE_HB_MIN = 30           # agent_status rows older than this don't resolve a session
RAW_CAPTURE_LINES = 200     # scrollback captured per pane (matches console panes.py)
TMUX_TIMEOUT_S = 5

# Coordinator bodies (the hub, Nazim) run in fixed tmux session names but — unlike
# lanes — do NOT self-register a tmux_session in agent_status, so they're mapped
# explicitly. HOST-AGNOSTIC by construction: only sessions actually live on THIS
# host are ever published, so the same map is correct on every host. On the fleet
# hub 'orch' resolves; on Nazim's Mac Mini 'nazim' resolves; neither sees the
# other's session. These ids match the console's coordinator cards
# (console/db.py build_coordinators_query + app.py _COORD_DB_PEEK).
COORDINATOR_SESSIONS = {
    "orch": "cc-orchestrator",   # the fleet hub
    "nazim": "orch-console",     # Nazim, the operator's CTO console (runs on the Mac Mini)
}


# ── tmux (read-only) ──────────────────────────────────────────────────────────
def _tmux_bin() -> str:
    """Absolute tmux path first: launchd runs us with a minimal PATH that omits
    Homebrew, so a bare 'tmux' silently fails and every session looks dead. Same
    fix console/panes.py uses."""
    for candidate in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "tmux"


_TMUX = _tmux_bin()


def live_sessions() -> List[str]:
    """Real, currently-live tmux session names on THIS host — the sole source of
    truth for what capture_pane is allowed to read (an unlisted name never reaches
    the subprocess)."""
    try:
        r = subprocess.run(
            [_TMUX, "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=TMUX_TIMEOUT_S,
        )
        if r.returncode != 0:
            return []
        return [s for s in r.stdout.splitlines() if s]
    except Exception:
        return []


def capture_pane(session: str) -> Optional[str]:
    """Raw pane text (last RAW_CAPTURE_LINES) of `session`'s active pane, or None
    if the capture fails. argv LIST (never a shell string) so the exact-match
    '=' target prefix is passed literally — no zsh =-expansion, no injection.
    Caller has already confirmed `session` is in the live-sessions list."""
    try:
        r = subprocess.run(
            [_TMUX, "capture-pane", "-t", f"={session}:0.0", "-p", "-S", f"-{RAW_CAPTURE_LINES}"],
            capture_output=True, text=True, timeout=TMUX_TIMEOUT_S,
        )
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


# ── identity (session -> agent_id) ────────────────────────────────────────────
def resolve_session_map(conn: "psycopg.Connection", local: List[str]) -> Dict[str, str]:
    """{tmux_session: agent_id} for the local sessions we should publish.

    Data-driven off agent_status (the same table + tmux_session column the console
    uses to peek a lane), scoped to sessions live on THIS host and to a fresh
    heartbeat so a dead ghost's stale session name never wins. DISTINCT ON keeps
    the freshest row per session. Coordinator sessions are then forced to their
    canonical ids (authoritative over any agent_status guess)."""
    mapping: Dict[str, str] = {}
    if local:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (tmux_session) tmux_session, agent_id
              FROM agent_status
             WHERE tmux_session = ANY(%s)
               AND last_heartbeat > now() - (%s * interval '1 minute')
               AND status <> 'offline'
             ORDER BY tmux_session, last_heartbeat DESC
            """,
            [local, STALE_HB_MIN],
        ).fetchall()
        mapping = {sess: aid for sess, aid in rows}
    # Coordinator bodies are authoritative (they don't self-register a
    # tmux_session). Only the ones live on THIS host apply — host-agnostic.
    for sess, agent_id in COORDINATOR_SESSIONS.items():
        if sess in local:
            mapping[sess] = agent_id
    return mapping


# ── publish tick ──────────────────────────────────────────────────────────────
def publish_once(conn: "psycopg.Connection") -> int:
    """One sweep: capture every resolvable live session and UPSERT its pane.
    Returns the number of rows written this tick."""
    local = live_sessions()
    mapping = resolve_session_map(conn, local)
    written = 0
    for session, agent_id in mapping.items():
        raw = capture_pane(session)
        if raw is None or not raw.strip():
            # session vanished mid-tick or blank pane — skip, never clobber a good row
            continue
        conn.execute(
            """
            INSERT INTO coordinator_panes (agent_id, pane_text, captured_at)
            VALUES (%s, %s, now())
            ON CONFLICT (agent_id) DO UPDATE
              SET pane_text = EXCLUDED.pane_text, captured_at = now()
            """,
            [agent_id, raw],
        )
        written += 1
    return written


def main() -> int:
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.stderr.write("coordinator_pane_publisher: no DATABASE_URL/SUPABASE_DB_URL\n")
        return 1

    sys.stderr.write(
        f"coordinator_pane_publisher: up (interval={INTERVAL_S}s, tmux={_TMUX})\n"
    )
    sys.stderr.flush()

    conn: Optional["psycopg.Connection"] = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = psycopg.connect(dsn, autocommit=True, connect_timeout=10)
            publish_once(conn)
        except Exception as e:
            # Never die on a bad tick — log, drop the (possibly wedged) connection,
            # and reconnect next tick. launchd KeepAlive is the outer safety net.
            sys.stderr.write(f"coordinator_pane_publisher: tick error: {type(e).__name__}: {e}\n")
            sys.stderr.flush()
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
