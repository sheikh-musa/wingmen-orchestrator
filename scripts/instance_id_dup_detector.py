#!/usr/bin/env python3
"""instance_id_dup_detector.py — detect two lanes claiming one instance id (orch-console bus #57914).

Incident 2026-10-07: tmux session cosem-platform-adhoc was cc-cosem-platform-5, relaunched
(`claude --resume`, same session) and the allocator handed it -3; cosem-platform-author then
took -5. The resumed conversation still believed it was -5, so bus rows addressed to -5 went
to the wrong lane. The allocator is now sticky per tmux session (scripts/lib/auto_agent_id.py);
this detector is the independent backstop that notices when identities have drifted anyway.

Findings (pure classifiers over agent_status rows — unit-tested, no DB):
  (a) dup_live    — one tmux session (same host) has 2+ agent_status rows with a FRESH
                    heartbeat (<10 min) and status != 'offline': two identities live in one lane.
  (b) stale_read  — bus rows addressed to <base>-M were read_at-stamped in the last 60 min while
                    <base>-M's own agent_status row is stale (>30 min) or offline: someone is
                    reading mail for an id nobody live owns.
                    ATTRIBUTION LIMIT: agent_messages records WHEN a row was read, not WHO read it.
                    We attribute the reader by inference: <base>-M's row still names the tmux
                    session that last held it; if that session (same host, same base) now has a
                    FRESH row for a different <base>-N, the lane there is (almost certainly) still
                    reading its OLD id's inbox -> attributed=True, paged. If no such session
                    exists, the finding is reported as the raw "rows-to-stale-id-being-read"
                    signal (attributed=False) and is NOT paged (readers we can't attribute
                    include the console / reply_to read-stamping; paging them would be noise).
                    Rows older than 72h are excluded (fleet_health.py's dead-letter archive
                    stamps read_at on those). Blind spot: a lane reading mail for an id that is
                    currently LIVE in another session (the -5 half of the incident) cannot be
                    seen from bus data at all — that is what the sticky allocator prevents.

Modes:
  --dry-run   read + classify + print findings as JSON; never writes state, never pages.
  (default)   page orch-console ONCE per finding (P1, requires_response) via bus_send.send(),
              deduped by a state file with a 60-min cooldown per finding key.
FAIL LOUD: any exception -> stderr + non-zero exit. No `|| true` on any state-changing path.
No launchd plist yet — scheduling is wired after review.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

ORCH = Path(__file__).resolve().parent.parent
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

FRESH_MINUTES = 10
STALE_MINUTES = 30          # same "reclaimable" bound the allocator uses
READ_WINDOW_MINUTES = 60
ARCHIVE_EXCLUDE_HOURS = 72  # fleet_health.py ARCHIVE_MIN_AGE_H — archive stamps read_at
COOLDOWN_MINUTES = 60
FROM_AGENT = "cc-fleet-health"
TO_AGENT = "orch-console"
STATE_FILE = ORCH / "logs" / "instance_id_dup_detector_state.json"


# ── pure core (unit-tested; no DB) ────────────────────────────────────────────
def _age_min(row: dict, now: dt.datetime) -> float:
    hb = row.get("last_heartbeat")
    if hb is None:
        return float("inf")
    return (now - hb).total_seconds() / 60.0


def is_fresh_live(row: dict, now: dt.datetime, fresh_minutes: int = FRESH_MINUTES) -> bool:
    return row.get("status") != "offline" and _age_min(row, now) < fresh_minutes


def is_stale_or_offline(row: dict, now: dt.datetime, stale_minutes: int = STALE_MINUTES) -> bool:
    return row.get("status") == "offline" or _age_min(row, now) > stale_minutes


def find_duplicate_live_claims(rows: list[dict], now: dt.datetime,
                               fresh_minutes: int = FRESH_MINUTES) -> list[dict]:
    """(a) one (host, tmux_session) with 2+ fresh, non-offline rows."""
    groups: dict[tuple, list[str]] = {}
    for r in rows:
        sess = r.get("tmux_session")
        if not sess or not is_fresh_live(r, now, fresh_minutes):
            continue
        groups.setdefault((r.get("host"), sess), []).append(r["agent_id"])
    out = []
    for (host, sess), ids in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        if len(ids) < 2:
            continue
        ids = sorted(ids)
        out.append({
            "kind": "dup_live", "host": host, "tmux_session": sess, "agent_ids": ids,
            "key": f"dup_live:{host}:{sess}:{','.join(ids)}",
        })
    return out


def find_stale_id_reads(rows: list[dict], reads: list[dict], now: dt.datetime,
                        fresh_minutes: int = FRESH_MINUTES,
                        stale_minutes: int = STALE_MINUTES) -> list[dict]:
    """(b) recent reads of rows addressed to a stale/offline instance id.

    reads: [{to_agent, n_read, last_read_at}] (already windowed by the SQL).
    Attributed when the stale id's last tmux session (same host + base) now has a
    FRESH row for a different id of the same base.
    """
    by_id = {r["agent_id"]: r for r in rows}
    out = []
    for rd in reads:
        stale = by_id.get(rd["to_agent"])
        if stale is None or not is_stale_or_offline(stale, now, stale_minutes):
            continue
        base, sess, host = stale.get("base_agent_id"), stale.get("tmux_session"), stale.get("host")
        live_ids = []
        if sess:
            live_ids = sorted(
                r["agent_id"] for r in rows
                if r["agent_id"] != stale["agent_id"]
                and r.get("base_agent_id") == base
                and r.get("tmux_session") == sess
                and r.get("host") == host
                and is_fresh_live(r, now, fresh_minutes)
            )
        out.append({
            "kind": "stale_read", "attributed": bool(live_ids),
            "stale_id": stale["agent_id"], "stale_status": stale.get("status"),
            "tmux_session": sess, "host": host, "live_ids": live_ids,
            "n_read": rd.get("n_read"), "last_read_at": rd.get("last_read_at"),
            "key": f"stale_read:{stale['agent_id']}:{host}:{sess}",
        })
    return out


def pageable(findings: list[dict]) -> list[dict]:
    """dup_live always pages; stale_read only when attributed (see module docstring)."""
    return [f for f in findings if f["kind"] == "dup_live" or f.get("attributed")]


def due_for_page(findings: list[dict], state: dict, now: dt.datetime,
                 cooldown_minutes: int = COOLDOWN_MINUTES) -> list[dict]:
    out = []
    for f in findings:
        last = state.get(f["key"])
        if last and (now - dt.datetime.fromisoformat(last)).total_seconds() < cooldown_minutes * 60:
            continue
        out.append(f)
    return out


def render_page(f: dict) -> tuple[str, str]:
    """(subject, body). Subject = one-line plain TL;DR; body opens with TL;DR."""
    if f["kind"] == "dup_live":
        ids = ", ".join(f["agent_ids"])
        subject = f"Two instance ids live in one lane: tmux {f['tmux_session']} on {f['host']} = {ids}"
        tldr = (f"TL;DR: tmux session {f['tmux_session']} ({f['host']}) has {len(f['agent_ids'])} "
                f"FRESH agent_status rows ({ids}) — one lane is heartbeating as more than one id.")
        what = ("Why it matters: bus rows to one of these ids may be read by the wrong conversation "
                "(the 2026-10-07 cosem-platform -5/-3 swap, orch-console #57914).\n"
                "What to do: pane-probe the session, ask the lane which id it believes it is, and "
                "offline the id it is NOT (admin_mark_offline) so the bus routes to one owner.")
    else:
        live = ", ".join(f["live_ids"])
        subject = (f"Lane {f['tmux_session']} (now {live}) is reading bus mail for its old id "
                   f"{f['stale_id']}")
        tldr = (f"TL;DR: {f.get('n_read')} bus row(s) to {f['stale_id']} ({f['stale_status']}/stale) "
                f"were read in the last {READ_WINDOW_MINUTES} min; that id last belonged to tmux "
                f"{f['tmux_session']} ({f['host']}), which is now live as {live}.")
        what = ("Why it matters: the lane's resumed conversation still believes it is "
                f"{f['stale_id']}, so it reads (and may act on) mail addressed to an id the "
                "allocator considers free (orch-console #57914).\n"
                "Attribution is INFERRED (agent_messages has no reader column): the stale id's "
                "row still names this session, and this session now heartbeats as another id.\n"
                f"What to do: tell the lane its id is now {live} (or re-launch it so the sticky "
                f"allocator hands {f['stale_id']} back), and re-address any open threads.")
    body = f"{tldr}\n\n{what}\n\nfinding key: {f['key']}\n(from instance_id_dup_detector.py)"
    return subject[:200], body


# ── IO ────────────────────────────────────────────────────────────────────────
def _dsn() -> str:
    from scripts.lib.substrate_dsn import dsn_from_env_file  # op#24342 file-first
    return dsn_from_env_file(str(ORCH / ".env"))


def fetch(dsn: str) -> tuple[list[dict], list[dict], dt.datetime]:
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT now()")
        now = cur.fetchone()[0]
        cur.execute(
            "SELECT agent_id, base_agent_id, tmux_session, host, status, last_heartbeat"
            " FROM agent_status WHERE base_agent_id IS NOT NULL"
        )
        cols = ["agent_id", "base_agent_id", "tmux_session", "host", "status", "last_heartbeat"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute(
            """SELECT m.to_agent, count(*), max(m.read_at)
                 FROM agent_messages m
                 JOIN agent_status s ON s.agent_id = m.to_agent
                WHERE m.read_at > now() - (%s * interval '1 minute')
                  AND m.created_at > now() - (%s * interval '1 hour')
                GROUP BY m.to_agent""",
            (READ_WINDOW_MINUTES, ARCHIVE_EXCLUDE_HOURS),
        )
        reads = [{"to_agent": a, "n_read": n, "last_read_at": t} for a, n, t in cur.fetchall()]
    return rows, reads, now


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())  # corrupt state -> raise (fail loud), never silently re-page


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _jsonable(f: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in f.items()}


def run(dry_run: bool, state_path: Path = STATE_FILE) -> int:
    rows, reads, now = fetch(_dsn())
    findings = find_duplicate_live_claims(rows, now) + find_stale_id_reads(rows, reads, now)
    to_page = pageable(findings)
    print(json.dumps({"now": now.isoformat(), "dry_run": dry_run,
                      "findings": [_jsonable(f) for f in findings],
                      "pageable_keys": [f["key"] for f in to_page]}, indent=2))
    if dry_run:
        return 0

    from scripts.bus_send import send
    state = load_state(state_path)
    for f in due_for_page(to_page, state, now):
        subject, body = render_page(f)
        row_id, _ = send(FROM_AGENT, TO_AGENT, "update", subject, body, "P1", req=True)
        state[f["key"]] = now.isoformat()
        save_state(state_path, state)  # persist per page: a later failure must not re-page this one
        print(f"PAGED {TO_AGENT} bus id={row_id} key={f['key']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="classify + print only; no state write, no page")
    ap.add_argument("--state-file", default=str(STATE_FILE))
    args = ap.parse_args(argv)
    try:
        return run(args.dry_run, Path(args.state_file))
    except Exception as e:  # FAIL LOUD: surface + non-zero, never swallow
        sys.stderr.write(f"instance_id_dup_detector FAILED: {type(e).__name__}: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
