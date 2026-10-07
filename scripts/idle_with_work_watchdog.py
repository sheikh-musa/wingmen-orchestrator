#!/usr/bin/env python3
"""idle_with_work_watchdog.py — no lane idles while it owns ACTIONABLE work.

Musa op#24477/#24479, orch-console #48417/#48432. Incident: cc-irsyad-coord went idle
("resume on next trigger") while it owned ready, spec'd work (#114/#119/#129); the
autoscaler saw demand=0 and nothing noticed until Musa did. This enforces it in code.

Every run (launchd, 15 min), per lane: gather OWNED OPEN WORK (held_commitments pending/
fired + unclaimed coord_dispatch_queue for coords + operator_backlog rows naming it +
unread bus rows), drop items that carry an explicit blocked_on (external dep), and if the
lane's pane is idle (>=15m no activity) AND actionable work remains, walk the ladder:
  tick 1  -> nudge the lane with the concrete item list (lane_nudge, gzb-aware)
  tick 2  (still idle, SAME items) -> P1 page orch-console with the list
  tick 3+ -> page + mark it on the fleet console
State (per lane: items_hash, consecutive_ticks) lives in idle_with_work_state (mig 087);
it resets the moment the lane acts or its actionable set changes.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ORCH_DIR not in sys.path:
    sys.path.insert(0, ORCH_DIR)  # so `from scripts.lib...` resolves whether run as -m or as a path
IDLE_SECONDS = 15 * 60
PHASE1_LANES = ["cc-irsyad-coord", "cc-irsyad-1"]  # cc-irsyad-2 permanently reaped by the hub
# (#53964, 2026-10-06) -- autoscaler respins fresh worker ids on demand, never this one again.
# Removed per orch-console #54066 (de-register at the source, don't keep papering nudges to
# a dead instance). If a future cc-irsyad-N needs phase-1 idle-with-work coverage, add it
# explicitly here -- don't reintroduce a reaped id.
COORD_LANES = {"cc-irsyad-coord"}
# The REAL nudge tool: bash, POSITIONAL args (<tmux-session> "<message>"), verified-submit
# with its own retry/ceiling. There is NO scripts/lane_nudge.py — a prior version called
# one, which failed silently (cc-quality #48596). Mirror priority_sla_watchdog.py's shape.
LANE_NUDGE = os.path.join(ORCH_DIR, "scripts", "lane_nudge.sh")


def _dsn():
    from scripts.lib.substrate_dsn import dsn_from_env_file  # op#24342 file-first
    return dsn_from_env_file(os.path.join(ORCH_DIR, ".env"))


def lane_map(conn) -> dict:
    """base_agent_id -> tmux lane (from fleet_lanes). Mirrors priority_sla_watchdog.lane_map.
    A lane absent here (e.g. cc-irsyad-1/cc-irsyad-2 have no fleet_lanes row) is UNREACHABLE
    by this watchdog: _nudge treats it as a delivery failure and run_lane escalates — never
    a silent skip."""
    with conn.cursor() as cur:
        cur.execute("SELECT base_agent_id, lane FROM fleet_lanes WHERE base_agent_id IS NOT NULL")
        return {a: l for a, l in cur.fetchall()}


# ── pure core (unit-tested; no DB) ────────────────────────────────────────────
def _is_blocked(item):
    bo = item.get("blocked_on")
    return bool(bo and str(bo).strip())


def classify(work_items, pane_idle):
    """Split owned work into actionable vs blocked and decide whether to fire.

    An item is BLOCKED only if it carries a non-empty blocked_on (an explicit external
    dependency). should_fire iff the pane is idle AND at least one item is actionable.
    """
    actionable = [w for w in work_items if not _is_blocked(w)]
    blocked = [w for w in work_items if _is_blocked(w)]
    return {"actionable": actionable, "blocked": blocked,
            "should_fire": bool(pane_idle and actionable)}


def items_hash(actionable):
    refs = sorted(str(w.get("ref", "")) for w in actionable)
    return hashlib.sha256("\n".join(refs).encode()).hexdigest()[:16]


# ── gathering (DB) ────────────────────────────────────────────────────────────
def gather_owned_work(cur, lane):
    items = []
    cur.execute("SELECT id, title, blocked_on FROM held_commitments "
                "WHERE owner_agent=%s AND status IN ('pending','fired')", (lane,))
    for i, title, bo in cur.fetchall():
        items.append({"ref": "commitment#%s" % i, "summary": title, "blocked_on": bo,
                      "source": "held_commitments"})
    if lane in COORD_LANES:
        cur.execute("SELECT id, title, blocked_on FROM coord_dispatch_queue "
                    "WHERE claimed_by IS NULL AND done_at IS NULL")
        for i, title, bo in cur.fetchall():
            items.append({"ref": "dispatch#%s" % i, "summary": title, "blocked_on": bo,
                          "source": "coord_dispatch_queue", "in_queue": True})
    cur.execute("SELECT id, ask, blocked_on FROM operator_backlog "
                "WHERE status NOT IN ('done','cancelled','closed') "
                "AND (ask ILIKE %s OR note ILIKE %s OR op_ref ILIKE %s)",
                ("%" + lane + "%", "%" + lane + "%", "%" + lane + "%"))
    for i, ask, bo in cur.fetchall():
        items.append({"ref": "backlog#%s" % i, "summary": ask, "blocked_on": bo,
                      "source": "operator_backlog"})
    cur.execute("SELECT id, subject FROM agent_messages "
                "WHERE to_agent=%s AND read_at IS NULL", (lane,))
    for i, subj in cur.fetchall():
        items.append({"ref": "bus#%s" % i, "summary": subj, "blocked_on": None,
                      "source": "agent_messages"})
    return items


def lane_idle(cur, lane):
    """Cross-host idle proxy: no telemetry/outbound-bus activity for >= IDLE_SECONDS.

    Uses the substrate (works for gzb + Mini lanes without an ssh pane-probe): the most
    recent of the lane's cc_session_costs event, its last outbound bus message, and its
    agent_status heartbeat. A pane-probe refinement can tighten this later.
    """
    cur.execute(
        "SELECT EXTRACT(EPOCH FROM (now() - GREATEST("
        "  COALESCE((SELECT max(COALESCE(ended_at,started_at)) FROM cc_session_costs WHERE cc_identity=%s), 'epoch'),"
        "  COALESCE((SELECT max(created_at) FROM agent_messages WHERE from_agent=%s), 'epoch'),"
        "  COALESCE((SELECT max(last_heartbeat) FROM agent_status WHERE agent_id=%s OR base_agent_id=%s), 'epoch')"
        ")))", (lane, lane, lane, lane))
    secs = cur.fetchone()[0]
    return (secs is not None and secs >= IDLE_SECONDS), (float(secs) if secs is not None else None)


def _nudge(lane, actionable, coord_unqueued, dry, lane_map):
    """Deliver the idle-with-work nudge to LANE via the REAL lane_nudge.sh.

    Returns True ONLY on a verified delivery (lane_nudge.sh rc==0). Returns False
    for every failure mode — lane has no tmux-session mapping (unreachable), the
    script returns non-zero (no such session / could not verify / parked / ceiling),
    or the subprocess errors. The caller MUST treat False as a failed nudge and
    escalate; a nudge that didn't land is never reported as success (cc-quality #48596).
    """
    lines = ["[idle-with-work] You are idle but OWN %d actionable item(s):" % len(actionable)]
    for it in actionable[:20]:
        lines.append("  - %s: %s" % (it["ref"], (it.get("summary") or "")[:80]))
    if coord_unqueued:
        lines.append("QUEUE these into coord_dispatch_queue (not yet dispatched): "
                     + ", ".join(it["ref"] for it in coord_unqueued[:20]))
    lines.append("Pick one up now, or set blocked_on (external dep + since-when) if truly blocked.")
    msg = "\n".join(lines)
    if dry:
        session = (lane_map or {}).get(lane, "<unmapped>")
        print("[DRY nudge %s -> tmux:%s]\n%s" % (lane, session, msg)); return True
    session = (lane_map or {}).get(lane)
    if not session:
        print("[idle-with-work] NUDGE FAILED: lane %s has no fleet_lanes tmux mapping — "
              "unreachable, escalating" % lane, file=sys.stderr)
        return False
    try:
        r = subprocess.run([LANE_NUDGE, session, msg],
                           capture_output=True, text=True, cwd=ORCH_DIR, timeout=90)
    except Exception as e:  # noqa: BLE001 — any failure is a failed delivery, surfaced loud
        print("[idle-with-work] NUDGE FAILED: lane_nudge.sh raised for %s (tmux:%s): %r"
              % (lane, session, e), file=sys.stderr)
        return False
    if r.returncode != 0:
        print("[idle-with-work] NUDGE FAILED: lane_nudge.sh rc=%d for %s (tmux:%s) — %s"
              % (r.returncode, lane, session, (r.stderr or "").strip()[:200]), file=sys.stderr)
        return False
    return True


def _page(subject, body, dry):
    if dry:
        print("[DRY page orch-console] %s\n%s" % (subject, body)); return
    subprocess.run([sys.executable, os.path.join(ORCH_DIR, "scripts", "bus_send.py"),
                    "--to", "orch-console", "--from", "cc-fleet-health", "--type", "blocker",
                    "--priority", "P1", "--req", "--subject", subject],
                   input=body.encode(), cwd=ORCH_DIR, timeout=30)


def run_lane(cur, lane, lane_map, dry=False):
    items = gather_owned_work(cur, lane)
    idle, idle_secs = lane_idle(cur, lane)
    c = classify(items, idle)
    actionable = c["actionable"]
    result = {"lane": lane, "idle": idle, "idle_secs": idle_secs,
              "actionable": [w["ref"] for w in actionable],
              "blocked": [w["ref"] for w in c["blocked"]], "fired": None}
    if not c["should_fire"]:
        if not dry:
            cur.execute("UPDATE idle_with_work_state SET consecutive_ticks=0, updated_at=now() WHERE lane=%s", (lane,))
        result["fired"] = "none"
        return result
    h = items_hash(actionable)
    cur.execute("SELECT items_hash, consecutive_ticks FROM idle_with_work_state WHERE lane=%s", (lane,))
    row = cur.fetchone()
    tick = (row[1] + 1) if (row and row[0] == h) else 1
    coord_unqueued = [w for w in actionable if w.get("source") != "coord_dispatch_queue"] if lane in COORD_LANES else []
    if tick == 1:
        if _nudge(lane, actionable, coord_unqueued, dry, lane_map):
            result["fired"] = "nudge"
        else:
            # The nudge could NOT be delivered (lane_nudge.sh failed, or lane has no tmux
            # mapping). Never a silent skip — fail LOUD and escalate to the operator now
            # rather than reporting a phantom "nudge" and waiting a full extra tick.
            _page("[idle-with-work] %s idle %dm with %d actionable — NUDGE UNDELIVERABLE, escalating" % (lane, (idle_secs or 0)//60, len(actionable)),
                  "%s is idle and owns actionable work, but the tick-1 nudge could NOT be delivered (lane_nudge.sh returned non-zero, or the lane has no fleet_lanes tmux mapping). Escalating directly:\n%s" % (lane, "\n".join("  - %s: %s" % (w["ref"], (w.get("summary") or "")[:80]) for w in actionable)), dry)
            result["fired"] = "nudge-failed->page"
    elif tick == 2:
        _page("[idle-with-work] %s idle %dm with %d actionable items — nudged, still idle" % (lane, (idle_secs or 0)//60, len(actionable)),
              "%s has been idle and owns actionable work after a nudge:\n%s" % (lane, "\n".join("  - %s: %s" % (w["ref"], (w.get("summary") or "")[:80]) for w in actionable)), dry)
        result["fired"] = "page"
    else:
        _page("[idle-with-work] %s STILL idle (tick %d) with %d actionable — page + console-mark" % (lane, tick, len(actionable)),
              "%s idle for %d ticks with unactioned owned work:\n%s\n(marking on the fleet console)" % (lane, tick, "\n".join("  - %s: %s" % (w["ref"], (w.get("summary") or "")[:80]) for w in actionable)), dry)
        result["fired"] = "page+console-mark"
    if not dry:
        cur.execute(
            "INSERT INTO idle_with_work_state (lane, items_hash, items, consecutive_ticks, last_nudge_at, updated_at) "
            "VALUES (%s,%s,%s,%s,now(),now()) ON CONFLICT (lane) DO UPDATE SET "
            "items_hash=EXCLUDED.items_hash, items=EXCLUDED.items, consecutive_ticks=%s, last_nudge_at=now(), updated_at=now()",
            (lane, h, json.dumps([w["ref"] for w in actionable]), tick, tick))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lanes", nargs="*", default=PHASE1_LANES)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    import psycopg
    out = []
    with psycopg.connect(_dsn()) as conn:
        conn.autocommit = not args.dry_run
        lmap = lane_map(conn)
        with conn.cursor() as cur:
            for lane in args.lanes:
                out.append(run_lane(cur, lane, lmap, dry=args.dry_run))
    for r in out:
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
