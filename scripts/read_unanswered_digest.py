#!/usr/bin/env python3
"""Report-only digest: lanes that are IDLE but own READ-BUT-UNANSWERED work.

WHY THIS EXISTS (orch-console #88653, ruling #88660)
----------------------------------------------------
On 2026-11-01 a lane (cc-hifz-1) sat with a requires_response row that had been
READ 38 hours earlier and never answered. No detector fired. Three independent
reasons, each verified against the live code:

  1. `idle_with_work_watchdog.py:29` hardcodes PHASE1_LANES = two lanes. Every
     other lane is never iterated at all.
  2. `gather_owned_work()` there reads `agent_messages ... WHERE read_at IS NULL`
     — UNREAD only. A read-but-unanswered row is invisible to it by construction.
  3. `priority_sla_watchdog.py:88` drops anything older than MAX_VIOLATION_AGE_MIN
     (300 min), and its aged re-page net (lines 125-131) is defined for rows that
     are UNREAD past 6h. A read row qualifies for neither and decays silent.

and, the deepest one:

  4. `lane_idle()` derives "idle" from
         now - GREATEST(session-cost, last outbound msg, last_heartbeat)
     Heartbeat is a LIVENESS signal, refreshed every few minutes whether or not
     the lane does any work, so a live lane can never read as idle. Measured at
     the time of writing: 0 of 37 live lanes idle, max idle_secs 4 minutes.
     It only goes idle when the heartbeat STOPS — a dead lane, the one case a
     nudge cannot help.

THIS SCRIPT therefore does NOT use heartbeat as evidence of work. Idleness comes
from the PANE, via the fleet's own proven `lane_is_drained()` in
`scripts/lib/composer_capture.sh` (empty composer, no 'esc to interrupt', no
waiting-on-background-agents, no running background shell).

MAIL CAN BE BASE-ADDRESSED (cc-quality F1, #789 review): a row addressed to `cc-irsyad`
while four live instances run is the same class this digest exists to catch. It is
fetched too, and attributed EXACTLY ONCE per base — never N-fold across siblings — with
the base only called drained when every live sibling is drained.

REPORT-ONLY BY DESIGN. It never nudges, never mutates a lane, never pages. Console
explicitly ruled out widening PHASE1_LANES into a live fleet-wide nudge net
(#88660); a digest cannot wake-storm. `--post` sends one P3 row to orch-console.

A lane on another host (or with no fleet_lanes mapping) is reported as NOT
PROBEABLE rather than guessed at — an unprobeable lane is not evidence of idleness
(local-liveness-probe-says-nothing-about-another-hosts-row).

Usage:
    python3 -m scripts.read_unanswered_digest                 # print digest
    python3 -m scripts.read_unanswered_digest --json          # machine form
    python3 -m scripts.read_unanswered_digest --post          # send P3 to orch-console
"""
import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone

ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ORCH_DIR not in sys.path:
    sys.path.insert(0, ORCH_DIR)  # so `from scripts.lib...` resolves whether run as -m or as a path

COMPOSER_LIB = os.path.join(ORCH_DIR, "scripts", "lib", "composer_capture.sh")
BUS_SEND = os.path.join(ORCH_DIR, "scripts", "bus_send.py")

DEFAULT_MIN_AGE_H = 6.0
DEFAULT_PRIORITIES = ("P0", "P1", "P2")
DEFAULT_MAX_HEARTBEAT_MIN = 30
# A daily bus digest has to stay readable. The full set is available with --json or
# by raising these; the point of the text form is "which lanes need a human look".
MAX_ROWS_PER_LANE = 3
MAX_LANES_PER_SECTION = 8

# The reporter's OWN backlog is the standing responded_at reconcile pile (hundreds of
# already-handled-in-session rows) — including it adds no signal to a "which lanes need
# a human look" digest. Excluded by default; --include-self puts it back.
SELF_AGENT = "cc-fleet-health"
TMUX_TIMEOUT_S = 10
PANE_PROBE_TIMEOUT_S = 20

# Write-only producers: they fire rows that nobody owns, so a "reply" is not owed
# by a lane. Excluded by default (see responded-at-reconcile-gap-and-backlog).
WRITE_ONLY_PRODUCERS = ("commitment-sweeper",)

# Reads the pane text on stdin and returns exactly one token on stdout.
# lane_is_drained() sets CC_EMPTY inside composer_parse(), so it is the single
# proven definition of "not working" in the fleet — reused, never re-implemented.
_PANE_SHIM = r"""
lib="$1"
# shellcheck disable=SC1090
source "$lib" || { echo PROBE_ERROR; exit 3; }
txt="$(cat)"
if lane_is_drained "$txt"; then echo DRAINED; else echo BUSY; fi
"""


def _dsn():
    from scripts.lib.substrate_dsn import dsn_from_env_file  # op#24342 file-first
    return dsn_from_env_file(os.path.join(ORCH_DIR, ".env"))


def local_hostnames():
    """Hostnames that mean 'this machine' — the only ones we can tmux-probe."""
    names = {"Sheikhs-Mini", "localhost", "127.0.0.1"}
    try:
        h = socket.gethostname()
        names.add(h)
        names.add(h.split(".")[0])
    except OSError:
        pass
    if os.environ.get("RUD_LOCAL_HOST"):
        names.add(os.environ["RUD_LOCAL_HOST"])
    return names


def lane_map(cur):
    """base_agent_id -> tmux session name (from fleet_lanes)."""
    cur.execute("SELECT base_agent_id, lane FROM fleet_lanes WHERE base_agent_id IS NOT NULL")
    return {a: l for a, l in cur.fetchall()}


def session_for(agent_id, base, live_members, lmap, status_session=None):
    """PURE. (session, reason) to probe for `agent_id`; reason is None when we hold a
    session we are entitled to read.

    ORDER MATTERS, and the first entry is the one that is actually true:
      1. agent_status.tmux_session — the pane this instance is RUNNING IN. Authoritative,
         and populated for every live agent (37/37 on 2026-11-01). Measured against it,
         resolving from fleet_lanes instead probes the wrong pane for 8 of 37 live lanes
         — cc-irsyad-1/2/5 would all have been read as irsyad-worker-4, cosem-platform-2/4
         as cosem-platform-author.
      2. the fleet_lanes row keyed by the instance's own id (cc-quality-2 has one).
      3. the base's fleet_lanes row, but ONLY when the base has a single live body — then
         the base lane IS that body. fleet_lanes is keyed by base_agent_id, so it collapses
         a family's lanes onto one arbitrary row; borrowing it for one of several live
         instances would read sibling A's pane as evidence about sibling B, which is the
         false IDLE this digest exists to avoid. There we say "could not measure".
    """
    if status_session:
        return status_session, None
    own = lmap.get(agent_id)
    if own:
        return own, None
    base_sess = lmap.get(base)
    if not base_sess:
        return None, "no tmux_session and no fleet_lanes row for this instance or its base"
    if len(live_members) == 1:
        return base_sess, None
    return None, ("no tmux_session and %d live instances share base '%s'"
                  % (len(live_members), base))


def live_agents(cur, max_heartbeat_min):
    """Instance-level live agents: agent_id, base_agent_id, host, hb_age_min, tmux_session.

    tmux_session is carried through because it is the ONE authoritative instance->pane
    mapping: agent_status knows which pane each instance is actually running in, while
    fleet_lanes is keyed by base_agent_id and therefore collapses a lane family's rows
    onto one arbitrary lane (cc-irsyad has 12 lanes under one base; cosem-platform 6).
    """
    cur.execute(
        "SELECT agent_id, base_agent_id, host, "
        "round(EXTRACT(EPOCH FROM (now() - last_heartbeat)) / 60)::int, tmux_session "
        "FROM agent_status "
        "WHERE last_heartbeat > now() - (%s * interval '1 minute') "
        "  AND agent_id LIKE 'cc-%%' "
        "ORDER BY agent_id",
        (max_heartbeat_min,),
    )
    return cur.fetchall()


_RU_SQL = (
    "SELECT id, from_agent, message_type, priority, subject, "
    "       round(EXTRACT(EPOCH FROM (now() - read_at)) / 3600.0, 1) "
    "FROM agent_messages "
    "WHERE to_agent = ANY(%s) "
    "  AND requires_response "
    "  AND responded_at IS NULL "
    "  AND read_at IS NOT NULL "
    "  AND read_at < now() - (%s * interval '1 hour') "
    "  AND priority = ANY(%s) "
    "  AND coalesce(is_test, false) = false "
    "  AND from_agent <> ALL(%s) "
    "ORDER BY read_at"
)


def read_unanswered_any(cur, agent_ids, min_age_h, priorities):
    """Read-but-unanswered rows addressed to ANY of agent_ids.

    Takes a LIST because a lane's mail can arrive at its live instance id OR at its
    BASE id. bus_send refuses a base id when a live -N exists, so new mail is
    instance-addressed — but a lot of legacy mail is base-addressed, and keying the
    digest on the exact instance id made all of it structurally invisible (cc-quality
    F1). Both forms are fetched here; the CALLER decides attribution.
    """
    ids = [a for a in agent_ids if a]
    if not ids:
        return []
    cur.execute(_RU_SQL, (ids, min_age_h, list(priorities),
                          list(WRITE_ONLY_PRODUCERS)))
    return cur.fetchall()


def read_unanswered(cur, agent_id, min_age_h, priorities):
    """Rows addressed to exactly agent_id — one instance's own mailbox."""
    return read_unanswered_any(cur, [agent_id], min_age_h, priorities)


def pane_state(session):
    """'drained' | 'busy' | 'probe_error' for a local tmux session.

    Deliberately three-valued: probe_error must never be read as either state.
    """
    if not session:
        return "probe_error"
    try:
        cap = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", session],
            capture_output=True, text=True, timeout=TMUX_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "probe_error"
    if cap.returncode != 0:
        return "probe_error"
    try:
        probe = subprocess.run(
            ["/bin/bash", "-c", _PANE_SHIM, "_", COMPOSER_LIB],
            input=cap.stdout, capture_output=True, text=True,
            timeout=PANE_PROBE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "probe_error"
    token = (probe.stdout or "").strip().splitlines()
    token = token[-1] if token else ""
    if token == "DRAINED":
        return "drained"
    if token == "BUSY":
        return "busy"
    return "probe_error"


def collect(cur, min_age_h=DEFAULT_MIN_AGE_H, priorities=DEFAULT_PRIORITIES,
            max_heartbeat_min=DEFAULT_MAX_HEARTBEAT_MIN, probe=True,
            include_self=False):
    """Every live lane that owns read-but-unanswered work, with its pane state.

    Returns a dict:
        {"generated_at": iso, "thresholds": {...},
         "idle": [entry...],            # pane-drained: the actionable set
         "not_probeable": [entry...],   # owns the work but we cannot confirm idleness
         "busy": N}                     # owns the work but is demonstrably working
    Each entry: {agent, base, host, hb_age_min, pane, rows:[...]}
    """
    hosts = local_hostnames()
    lmap = lane_map(cur)
    idle, not_probeable, busy = [], [], 0

    def _state(hosts_, base, session, pane_states):
        """Conservative pane verdict for one identity.

        For a BASE entry (pane_states has one entry per live sibling) the lane only
        counts as DRAINED when EVERY live instance is drained — one busy sibling means
        the base's mail is being picked up, so calling it idle would be a false claim.
        """
        if not probe:
            return "not_probeable"
        # Anything we did not positively measure is unprobeable, and the SPECIFIC
        # reason is kept for the reader: a failed probe is not the same fact as
        # "this lane lives on another host". Neither may fall through to "drained".
        if any(s == "not_probeable" for s in pane_states):
            return "not_probeable"
        if any(s == "probe_error" for s in pane_states):
            return "probe_error"
        if any(s == "busy" for s in pane_states):
            return "busy"
        return "drained"

    def _entry(agent, base, host, hb_age, session, state, rows, reason=None):
        return {
            "agent": agent, "base": base, "host": host, "hb_age_min": hb_age,
            "pane": state, "session": session, "reason": reason,
            "rows": [
                {"id": r[0], "from": r[1], "type": r[2], "priority": r[3],
                 "subject": r[4], "age_h": float(r[5]) if r[5] is not None else None}
                for r in rows
            ],
        }

    def _file(entry):
        nonlocal busy
        if entry["pane"] == "drained":
            idle.append(entry)
        elif entry["pane"] == "busy":
            busy += 1
        else:
            not_probeable.append(entry)

    live = [a for a in live_agents(cur, max_heartbeat_min)
            if include_self or a[0] != SELF_AGENT]

    # Group BEFORE the instance pass: session_for() needs to know how many live
    # instances share a base to decide whether the base's pane may be borrowed.
    by_base = {}
    sessions = {}
    for agent_id, base_agent_id, host, hb_age, _sess in live:
        base = base_agent_id or agent_id
        by_base.setdefault(base, []).append((agent_id, host, hb_age))
        sessions[agent_id] = _sess

    # ── 1. instance-addressed mail: one entry per live instance, its own pane ──
    for agent_id, base_agent_id, host, hb_age, status_session in live:
        base = base_agent_id or agent_id
        rows = read_unanswered(cur, agent_id, min_age_h, priorities)
        if not rows:
            continue
        # Session resolution order is spelled out in session_for(): the instance's OWN
        # running pane first, then its own fleet_lanes row, and the base's row only when
        # the base has a single live body. Anything else reports "could not measure"
        # rather than borrowing a sibling's pane (orchestrator#790 follow-ups).
        session, reason = session_for(agent_id, base, by_base[base], lmap, status_session)
        states = ["not_probeable"] if not (probe and (host or "") in hosts and session) \
            else [pane_state(session)]
        _file(_entry(agent_id, base, host, hb_age, session,
                     _state(hosts, base, session, states), rows, reason))

    # ── 2. BASE-addressed mail: attributed ONCE per base, never N-fold ──
    # cc-quality F1. A row addressed to `cc-irsyad` while four live instances run
    # (cc-irsyad-1/2/3/5) must appear exactly once, not four times. We report it
    # under the base id, and only ever call it drained when EVERY live sibling is
    # drained — one busy sibling means the mail is being worked, so "idle" would be
    # a false claim.
    for base, members in sorted(by_base.items()):
        # A singleton that runs AS its base id is already covered in step 1.
        if len(members) == 1 and members[0][0] == base:
            continue
        rows = read_unanswered_any(cur, [base], min_age_h, priorities)
        if not rows:
            continue
        session = lmap.get(base)
        states = []
        reasons = set()
        for _aid, host, _hb in members:
            # Each sibling is probed on ITS OWN running pane — the same resolution as
            # step 1. A base entry is only idle when every live sibling is drained.
            m_session, m_reason = session_for(_aid, base, members, lmap, sessions.get(_aid))
            if m_reason:
                reasons.add(m_reason)
            if not (probe and (host or "") in hosts and m_session):
                states.append("not_probeable")
            else:
                states.append(pane_state(m_session))
        host = members[0][1] if len({m[1] for m in members}) == 1 else "mixed"
        hb_age = min((m[2] if m[2] is not None else 10 ** 9) for m in members)
        _file(_entry(base, base, host, hb_age, session,
                     _state(hosts, base, session, states), rows,
                     "; ".join(sorted(reasons)) or None))

    idle.sort(key=lambda e: max((x["age_h"] or 0) for x in e["rows"]), reverse=True)
    not_probeable.sort(key=lambda e: max((x["age_h"] or 0) for x in e["rows"]), reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "thresholds": {"min_age_h": min_age_h, "priorities": list(priorities),
                       "max_heartbeat_min": max_heartbeat_min},
        "idle": idle,
        "not_probeable": not_probeable,
        "busy": busy,
    }


def _row_line(row):
    subj = (row["subject"] or "").strip().replace("\n", " ")[:72]
    age = row["age_h"] if row["age_h"] is not None else 0
    return "  - #%s  %-3s %5.1fh  from %-22s %s" % (
        row["id"], row["priority"], age, row["from"], subj)


def _entry_lines(entry, max_rows=MAX_ROWS_PER_LANE):
    why = "" if entry["pane"] == "drained" else \
        "  [%s: host=%s session=%s]" % (entry["pane"], entry["host"], entry["session"])
    out = ["%s  (hb %sm)%s" % (entry["agent"], entry["hb_age_min"], why)]
    rows = entry["rows"]                             # already oldest-first per lane
    out.extend(_row_line(r) for r in rows[:max_rows])
    if len(rows) > max_rows:
        out.append("  ... +%d more row(s)" % (len(rows) - max_rows))
    return out


def _compact_section(title, entries, max_lanes=MAX_LANES_PER_SECTION):
    """One line per lane — for the section we could NOT probe, where dumping rows
    would imply more certainty than we have."""
    if not entries:
        return [title + " — none."]
    n_rows = sum(len(e["rows"]) for e in entries)
    out = ["%s — %d lane(s), %d row(s):" % (title, len(entries), n_rows)]
    for e in entries[:max_lanes]:
        oldest = max((r["age_h"] or 0) for r in e["rows"])
        reason = e.get("reason")
        out.append("  %-26s hb %sm  host=%-12s %d row(s), oldest %.1fh  [%s]%s"
                   % (e["agent"], e["hb_age_min"], e["host"], len(e["rows"]),
                      oldest, e["pane"], (" — " + reason) if reason else ""))
    if len(entries) > max_lanes:
        out.append("  ... +%d more lane(s) (use --json for the full set)"
                   % (len(entries) - max_lanes))
    return out


def _section(title, entries, max_rows=MAX_ROWS_PER_LANE,
             max_lanes=MAX_LANES_PER_SECTION):
    if not entries:
        return [title + " — none."]
    n_rows = sum(len(e["rows"]) for e in entries)
    out = ["%s — %d lane(s), %d row(s):" % (title, len(entries), n_rows)]
    for e in entries[:max_lanes]:
        out.extend(_entry_lines(e, max_rows))
    if len(entries) > max_lanes:
        out.append("  ... +%d more lane(s) (use --json for the full set)"
                   % (len(entries) - max_lanes))
    return out


def render(digest):
    """Human-readable digest. Empty means empty — never a reassuring sentence."""
    t = digest["thresholds"]
    idle = digest["idle"]
    np = digest["not_probeable"]

    if not idle and not np:
        return ("READ-BUT-UNANSWERED DIGEST — %s\n"
                "Nothing to report: no live lane owns a %s row read >%sh ago.\n"
                "(Probed pane-drained state. %d live lane(s) were reading-but-busy or clean.)\n"
                % (digest["generated_at"], "/".join(t["priorities"]),
                   t["min_age_h"], digest["busy"]))

    lines = ["READ-BUT-UNANSWERED DIGEST — %s" % digest["generated_at"],
             "Lanes that READ a %s requires_response row, never answered it, and have"
             % "/".join(t["priorities"]),
             "now sat on it for >%sh. Idleness is PANE-probed (lane_is_drained), never"
             % t["min_age_h"],
             "inferred from heartbeat. Report-only: nothing was nudged.",
             ""]

    lines.extend(_section("IDLE (pane-drained)", idle))
    lines.append("")

    if np:
        lines.extend(_compact_section(
            "NOT PROBEABLE — could not confirm idle "
            "(other host / no fleet_lanes session / probe failed)", np))
        lines.append("")

    if digest["busy"]:
        lines.append("%d lane(s) omitted: they own such work but their pane is BUSY."
                     % digest["busy"])
        lines.append("")

    lines.append("CAVEAT: a very old row is often the known responded_at stamping gap "
                 "(the reply was sent")
    lines.append("without --reply-to and never stamped the original), not a dropped ask. "
                 "Verify before acting.")
    return "\n".join(lines).rstrip() + "\n"


def post(body, subject, to="orch-console", dry_run=False):
    """Send the digest as ONE P3 update. No --body flag: body goes on stdin, so no
    shell quoting hazard and nothing is expanded (see self-backtick-eaten-busmsg)."""
    cmd = [sys.executable, BUS_SEND, "--to", to, "--type", "update",
           "--priority", "P3", "--subject", subject]
    if dry_run:
        cmd.append("--dry-run")
    env = dict(os.environ)
    env.setdefault("CC_BASE_AGENT_ID", "cc-fleet-health")
    proc = subprocess.run(cmd, input=body, capture_output=True, text=True, env=env)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-age-hours", type=float, default=DEFAULT_MIN_AGE_H,
                    help="how long a row must have been READ to count (default %(default)s)")
    ap.add_argument("--priorities", nargs="*", default=list(DEFAULT_PRIORITIES),
                    help="priorities to include (default P0 P1 P2)")
    ap.add_argument("--max-heartbeat-min", type=int, default=DEFAULT_MAX_HEARTBEAT_MIN,
                    help="a lane counts as live if its heartbeat is younger than this")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip tmux pane probing (everything becomes not_probeable)")
    ap.add_argument("--include-self", action="store_true",
                    help="include cc-fleet-health's own (standing reconcile-pile) rows")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--post", action="store_true",
                    help="also send the digest as a P3 update to --post-to")
    ap.add_argument("--post-to", default="orch-console", help="recipient for --post")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --post: resolve+validate the bus row, do not write it")
    args = ap.parse_args(argv)

    import psycopg
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            digest = collect(cur, args.min_age_hours, args.priorities,
                             args.max_heartbeat_min, probe=not args.no_probe,
                             include_self=args.include_self)

    text = json.dumps(digest, indent=2) + "\n" if args.json else render(digest)
    sys.stdout.write(text)

    if args.post:
        n_idle = len(digest["idle"])
        subj = ("read-but-unanswered digest: %d idle lane(s) own unanswered work"
                % n_idle if n_idle else
                "read-but-unanswered digest: none idle")
        rc = post(text, subj, to=args.post_to, dry_run=args.dry_run)
        if rc != 0:
            # Never a silent success on a failed send.
            print("read-unanswered-digest: POST FAILED rc=%s" % rc, file=sys.stderr)
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
