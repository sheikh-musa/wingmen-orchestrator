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

# bus #44697: check()'s INSERT hardcoded from_agent='orch-console' on every STALLED-BY-
# CONSTRUCTION row, so it misattributed itself as having come from the console (e.g. #44695
# arrived orch-console -> orch-console). Same misattribution class as bus #44547
# (commitment_sweeper, migration 076). Dedicated identity, registered by migration 078
# (agent_messages.from_agent FKs to agents.id -- must exist before the first row posts).
FROM_AGENT = "programme-stall-guard"


def _parse_payload(payload):
    """(data, corrupt). corrupt is True only when payload is present but does not parse as
    JSON at all -- distinct from a well-formed payload with no backlog_id (a checkpoint
    simply not tied to a programme). bus #44697: op#22300's cp#57 payload was broken by
    appending plain text after the JSON, and the guard silently treated the checkpoint as
    unclocked instead of flagging the corruption."""
    if not payload:
        return None, False
    if not isinstance(payload, str):
        return payload, False
    try:
        return json.loads(payload), False
    except (ValueError, TypeError):
        return None, True


def backlog_id_of(payload) -> int | None:
    """held_commitments.payload is TEXT; a checkpoint links by {"backlog_id": N}."""
    data, _ = _parse_payload(payload)
    value = data.get("backlog_id") if isinstance(data, dict) else None
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def is_corrupt_payload(payload) -> bool:
    """True when payload is present but fails to parse as JSON at all."""
    return _parse_payload(payload)[1]


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


def find_corrupt_checkpoints(commitments):
    """Live checkpoints (pending/fired) whose payload doesn't parse as JSON at all -- a link
    the guard could not verify, worth flagging explicitly rather than silently treating as
    "no link" (bus #44697). Pure, for tests.

    commitments: [{"id", "owner_agent", "title", "payload", "status", ...}]
    """
    return [c for c in commitments if c["status"] in LIVE_CHECKPOINT and is_corrupt_payload(c["payload"])]


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
    corrupt = find_corrupt_checkpoints(commitments)
    now = dt.datetime.now(dt.timezone.utc)
    state = _read_state()

    def _due(key):
        return now - dt.datetime.fromisoformat(state.get(key, "1970-01-01T00:00:00+00:00")) >= ESCALATE_EVERY

    due = [p for p in unclocked if _due(str(p["id"]))]
    corrupt_due = [c for c in corrupt if _due(f"cp#{c['id']}")]

    print(f"programme_stall_guard: open={sum(p['status'] in OPEN_STATUSES for p in programmes)} "
          f"unclocked={len(unclocked)} escalating={len(due)} corrupt={len(corrupt)} "
          f"corrupt_escalating={len(corrupt_due)} dry_run={dry_run}")
    if not due and not corrupt_due:
        return 0

    body_parts = []
    if due:
        lines = [
            f"• backlog#{p['id']} [{p['status']}] {p['ask'][:110]} (last touched {p['updated_at']:%Y-%m-%d})"
            for p in due
        ]
        body_parts.append(
            "These in-progress programmes have NO live checkpoint, so nothing will wake anyone about "
            "them. For each: arm the next checkpoint (held_commitments with payload.backlog_id), park "
            "it, or mark it done.\n\n" + "\n".join(lines)
        )
    if corrupt_due:
        corrupt_lines = [
            f"• checkpoint #{c['id']} ({c['owner_agent']}) has an invalid payload: {c['title'][:110]!r}"
            for c in corrupt_due
        ]
        body_parts.append(
            "These LIVE checkpoints have a payload that does not parse as JSON at all, so the guard "
            "cannot tell what programme (if any) they clock. Fix the payload or re-arm the "
            "checkpoint.\n\n" + "\n".join(corrupt_lines)
        )
    body = (
        "\n\n".join(body_parts)
        + "\n\nConvention: see the docstring of scripts/programme_stall_guard.py. Re-escalates in 24h if unchanged."
    )
    if dry_run:
        print(body)
        return 0

    subject_bits = []
    if due:
        subject_bits.append(f"{len(due)} programme(s) with no live checkpoint")
    if corrupt_due:
        subject_bits.append(f"{len(corrupt_due)} checkpoint(s) with an invalid payload")
    subject = "STALLED BY CONSTRUCTION: " + "; ".join(subject_bits)

    import psycopg  # noqa: WPS433
    with psycopg.connect(_dsn(), connect_timeout=20) as conn:
        conn.execute(
            """INSERT INTO agent_messages
                 (from_agent, to_agent, message_type, subject, body, requires_response, priority)
               VALUES (%s, 'orch-console', 'blocker', %s, %s, true, 'P1')""",
            (FROM_AGENT, subject, body),
        )
    for p in due:
        state[str(p["id"])] = now.isoformat()
    for c in corrupt_due:
        state[f"cp#{c['id']}"] = now.isoformat()
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
