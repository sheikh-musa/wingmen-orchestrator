#!/usr/bin/env python3
"""lane_watch.py — cheap fleet watcher for cc-orchestrator.

Runs under launchd every 5 min. ZERO model tokens: pure SQL + git + Telegram.
It alerts the operator (ops channel) ONLY when something actually changed since
the previous run:
  - a new message addressed to cc-orchestrator (e.g. cc-ihsanos build plan, cai
    ruling) — this is the unified "someone needs you" signal,
  - a cc-ihsanos lane status change (idle/blocked/offline) or stale heartbeat,
  - a new commit in the ihsanos repo.

A watermark in scripts/.lane_watch/state.json prevents repeat alerts. The first
run establishes the baseline (and sends one "armed" confirmation) so we never
alert on historical backlog. Waking the *model* on a timer to check a DB is the
anti-pattern this avoids — the model only spends tokens when this watcher pings.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.parse
import urllib.request

import psycopg
from dotenv import load_dotenv

ORCH = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ORCH / ".env")
DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
STATE = ORCH / "scripts" / ".lane_watch" / "state.json"
IHSANOS_REPO = pathlib.Path.home() / "wingmen" / "projects" / "ihsanos"
WATCH_AGENT = "cc-ihsanos-1"
STALE_HB_S = 900  # heartbeat older than this while 'working' = possible stall


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def tg_send(text: str) -> bool:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("OPS_GROUP_ID") or os.environ.get("MUSA_TELEGRAM_ID")
    if not tok or not chat:
        print("tg: no token/chat", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15)
        return True
    except Exception as e:
        print("tg send failed:", e, file=sys.stderr)
        return False


def git_head(repo: pathlib.Path):
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def git_new_commits(repo: pathlib.Path, since_sha: str):
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "log", "--oneline", f"{since_sha}..HEAD"],
            text=True, stderr=subprocess.DEVNULL).strip()
        return [ln for ln in out.splitlines() if ln]
    except Exception:
        return []


def main() -> int:
    if not DSN:
        print("no DSN", file=sys.stderr)
        return 1
    st = load_state()
    first_run = "last_msg_id" not in st
    alerts: list[str] = []

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        if first_run:
            cur.execute("SELECT COALESCE(MAX(id),0) FROM agent_messages WHERE to_agent='cc-orchestrator'")
            st["last_msg_id"] = cur.fetchone()[0]
        else:
            # CAI-RESP-259 Q5 de-dup: only alert on messages Realtime did NOT
            # already push to Telegram (forwarded_to_telegram_at IS NULL). This
            # retires the duplicated notification while KEEPING the unique
            # coverage — CC-worker→cc-orchestrator traffic (Realtime suppresses
            # CC-to-CC) and the belt-and-suspenders fallback if Realtime is down.
            cur.execute("""SELECT id, from_agent, message_type, requires_response, subject
                           FROM agent_messages
                           WHERE to_agent='cc-orchestrator' AND id > %s
                             AND forwarded_to_telegram_at IS NULL
                           ORDER BY id""", (st["last_msg_id"],))
            rows = cur.fetchall()
            if rows:
                st["last_msg_id"] = max(r[0] for r in rows)
                for r in rows:
                    rr = " [needs response]" if r[3] else ""
                    alerts.append(f"\U0001F4E8 msg {r[0]} from {r[1]} ({r[2]}){rr}: {r[4]}")

        cur.execute("""SELECT status, current_task,
                              round(extract(epoch from (now()-last_heartbeat)))::int
                       FROM agent_status WHERE agent_id=%s""", (WATCH_AGENT,))
        row = cur.fetchone()
        if row:
            status, task, hb = row
            if first_run:
                st["last_status"] = status
            elif status != st.get("last_status"):
                prev = st.get("last_status")
                st["last_status"] = status
                if status in ("idle", "blocked", "offline"):
                    alerts.append(f"\U0001F6D1 {WATCH_AGENT} -> {status} (was {prev}). task: {task}")
            if not first_run and status == "working" and hb is not None and hb > STALE_HB_S and not st.get("stall_alerted"):
                alerts.append(f"⏳ {WATCH_AGENT} heartbeat stale {hb}s while 'working' - possible stall.")
                st["stall_alerted"] = True
            if hb is not None and hb <= STALE_HB_S:
                st["stall_alerted"] = False

    head = git_head(IHSANOS_REPO)
    if head:
        if first_run:
            st["last_commit_sha"] = head
        elif st.get("last_commit_sha") and head != st["last_commit_sha"]:
            for c in git_new_commits(IHSANOS_REPO, st["last_commit_sha"]):
                alerts.append(f"✅ ihsanos commit: {c}")
            st["last_commit_sha"] = head

    save_state(st)

    if first_run:
        tg_send("\U0001F52D lane watch armed - will alert on cc-ihsanos/cai replies, lane status changes, and ihsanos commits.")
        print("baseline established; armed.")
        return 0
    if alerts:
        body = "\U0001F52D cc-orchestrator lane watch\n\n" + "\n".join(alerts) + "\n\n(open the cc-orchestrator session to act)"
        tg_send(body)
        print(body)
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
