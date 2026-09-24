#!/usr/bin/env python3
"""programme_stall_guard.py — an in-progress programme can't go quiet without someone hearing.

Musa op#22300 (2026-09-24): "lets ensure these audits and scans never stall again. our
substrate is as forgetful as humans." The 09-05 ihsanification audit and the 09-22 Fable cost
rollout were both greenlit, then silently stopped: nothing held their next step once the bodies
that knew about them recycled.

The clocks already exist. `held_commitments` + `nervous_system/commitment_sweeper.py` (loaded,
every 5 min) fire a checkpoint at its owner when it comes due, and escalate if it isn't
discharged. What was missing is the INVARIANT that connects a programme to a clock:

    every operator_backlog row that is in_progress / needs_you has at least one LIVE checkpoint
    (a held_commitments row with payload.backlog_id = that row, status pending or fired).

A programme with no live checkpoint is stalled BY CONSTRUCTION: nobody will be woken about it.
This guard finds those and tells the console (once a day per programme, not every run: a guard
whose output is noise gets muted). The console then either arms the next checkpoint, parks the
programme, or marks it done. There is no fourth option.

  --check    find programmes with no live checkpoint; escalate to orch-console (default)
  --digest   weekly summary to Musa via nazim_send.sh: each open programme, owner, last progress,
             next checkpoint, and anything quiet for more than 7 days
  --dry-run  print what would be sent, send nothing

Convention for arming a checkpoint (the payload is what makes the link):
  INSERT INTO held_commitments(owner_agent, title, payload, due_at, status, source_ref, created_by)
  VALUES ('<owner>', 'CHECKPOINT backlog#<id>: …', '{"backlog_id": <id>, "next_progress_expected": "…"}',
          now() + interval '1 day', 'pending', 'op#…', 'orch-console');
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STATE = ROOT / "state" / "programme_stall_guard.json"
OPEN_STATUSES = ("in_progress", "needs_you")
LIVE_CHECKPOINT = ("pending", "fired")
ESCALATE_EVERY = dt.timedelta(hours=24)
QUIET_AFTER = dt.timedelta(days=7)


def backlog_id_of(payload) -> int | None:
    """held_commitments.payload is TEXT; a checkpoint links by {"backlog_id": N}."""
    if not payload:
        return None
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        value = data.get("backlog_id") if isinstance(data, dict) else None
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def find_unclocked(programmes, commitments):
    """Open programmes that no live checkpoint points at. Pure, for tests.

    programmes:  [{"id", "status", ...}]
    commitments: [{"payload", "status", ...}]
    """
    clocked = {
        backlog_id_of(c["payload"])
        for c in commitments
        if c["status"] in LIVE_CHECKPOINT
    }
    return [p for p in programmes if p["status"] in OPEN_STATUSES and p["id"] not in clocked]


def last_progress(programme, commitments):
    """Latest discharged checkpoint for this programme, else the programme's own updated_at."""
    done = [
        c for c in commitments
        if c["status"] == "discharged" and backlog_id_of(c["payload"]) == programme["id"]
    ]
    if done:
        latest = max(done, key=lambda c: c["discharged_at"])
        return latest["discharged_at"], latest.get("discharge_note") or ""
    return programme["updated_at"], ""


def _dsn() -> str:
    # launchd doesn't source .env (same trap commitment_sweeper documents), so load it here and
    # fail LOUD rather than no-op silently when there's still no DSN.
    from dotenv import load_dotenv  # noqa: WPS433
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.exit("programme_stall_guard: MISSING DSN — refusing to no-op silently")
    return dsn


def _load():
    import psycopg  # noqa: WPS433
    conn = psycopg.connect(_dsn(), connect_timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT id, status, ask, op_ref, note, updated_at FROM operator_backlog")
    programmes = [dict(zip(("id", "status", "ask", "op_ref", "note", "updated_at"), r)) for r in cur.fetchall()]
    cur.execute("SELECT id, owner_agent, title, payload, due_at, status, discharged_at, discharge_note FROM held_commitments")
    keys = ("id", "owner_agent", "title", "payload", "due_at", "status", "discharged_at", "discharge_note")
    commitments = [dict(zip(keys, r)) for r in cur.fetchall()]
    conn.close()
    return programmes, commitments


def _read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def check(dry_run: bool) -> int:
    programmes, commitments = _load()
    unclocked = find_unclocked(programmes, commitments)
    now = dt.datetime.now(dt.timezone.utc)
    state = _read_state()
    due = [
        p for p in unclocked
        if now - dt.datetime.fromisoformat(state.get(str(p["id"]), "1970-01-01T00:00:00+00:00")) >= ESCALATE_EVERY
    ]
    print(f"programme_stall_guard: open={sum(p['status'] in OPEN_STATUSES for p in programmes)} "
          f"unclocked={len(unclocked)} escalating={len(due)} dry_run={dry_run}")
    if not due:
        return 0
    lines = [
        f"• backlog#{p['id']} [{p['status']}] {p['ask'][:110]} (last touched {p['updated_at']:%Y-%m-%d})"
        for p in due
    ]
    body = (
        "These in-progress programmes have NO live checkpoint, so nothing will wake anyone about "
        "them. For each: arm the next checkpoint (held_commitments with payload.backlog_id), park "
        "it, or mark it done.\n\n" + "\n".join(lines)
        + "\n\nConvention: see the docstring of scripts/programme_stall_guard.py. Re-escalates in 24h if unchanged."
    )
    if dry_run:
        print(body)
        return 0
    import psycopg  # noqa: WPS433
    with psycopg.connect(_dsn(), connect_timeout=20) as conn:
        conn.execute(
            """INSERT INTO agent_messages
                 (from_agent, to_agent, message_type, subject, body, requires_response, priority)
               VALUES ('orch-console', 'orch-console', 'blocker', %s, %s, true, 'P1')""",
            (f"STALLED BY CONSTRUCTION: {len(due)} programme(s) with no live checkpoint", body),
        )
    for p in due:
        state[str(p["id"])] = now.isoformat()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))
    return 0


def digest(dry_run: bool) -> int:
    programmes, commitments = _load()
    now = dt.datetime.now(dt.timezone.utc)
    open_ = sorted((p for p in programmes if p["status"] in OPEN_STATUSES), key=lambda p: p["id"])
    if not open_:
        return 0
    rows = []
    for p in open_:
        when, note = last_progress(p, commitments)
        live = [c for c in commitments if c["status"] in LIVE_CHECKPOINT and backlog_id_of(c["payload"]) == p["id"]]
        nxt = min(live, key=lambda c: c["due_at"]) if live else None
        quiet = now - when > QUIET_AFTER
        rows.append(
            f"{'⚠ ' if quiet or not nxt else ''}{p['ask'][:80]}\n"
            f"   last progress {when:%d %b}{(': ' + note[:90]) if note else ''}\n"
            f"   next: {('%s by %s' % (nxt['owner_agent'], nxt['due_at'].strftime('%d %b'))) if nxt else 'NOTHING SCHEDULED'}"
        )
    text = ("Weekly check on everything in progress (⚠ = quiet for over a week or nothing scheduled):\n\n"
            + "\n\n".join(rows))
    if dry_run:
        print(text)
        return 0
    subprocess.run([str(ROOT / "scripts" / "nazim_send.sh"), text], check=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    os.chdir(ROOT)
    return digest(args.dry_run) if args.digest else check(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
