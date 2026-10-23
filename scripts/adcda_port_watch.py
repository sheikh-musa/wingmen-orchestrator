#!/usr/bin/env python3
"""adcda_port_watch.py — monitor the LIVE ADCDA app repo for new features that
need porting into the cosem platform (operator directive op#7517, 2026-07-26).

The operator actively tweaks ~/wingmen/projects/cosem-adcda (github sheikh-musa/
cosem-adcda). This watcher fetches origin, and on new commits to WATCHED_BRANCHES
beyond the last-seen SHA, posts an attributable bus row to PORT_OWNER (the
single assigned port-watch lane) listing the new commits for port-assessment.

ROUTING (fixed 2026-10-19, orch-console #71671/cc-cosem-platform-3's #71670):
this used to post to the BARE BASE 'cc-cosem-platform', which fans out to every
platform instance (-1/-2/-3/-4). op#7517 port-watch was assigned to
cc-cosem-platform-1 ALONE back on 2026-10-11 (#62694/#62695) -- this script
predates that ruling (created 2026-07-26) and nobody updated its hardcoded
target after, so -3 kept getting + correctly declining duplicate pings (3
consecutive occurrences). This is a DIFFERENT bug class from the October
bare-base-identity fix (resolve_agent_id/agent_identity.py) -- that fixed
agents posting AS the bare base; this is a standing SCRIPT whose own to_agent
was hardcoded wrong. Fix is just targeting the actual owner.

DETECTION ONLY — the JUDGEMENT ("does this feature need porting, where does it fit")
is the lane's, on the bus row (CAI-603: fact out, judgement in). Read-only on the
ADCDA repo (fetch + log, never checkout/mutate).

Dead-man's-switch (feedback_monitors_need_deadmans_switch): any failure to reach the
repo / remote is logged LOUDLY and posted as a DEGRADE row, never silently swallowed —
a monitor that can't see must say so, not report "no changes".

Run manually to establish baseline; schedule via launchd for ongoing monitoring.
"""
from __future__ import annotations
import os, subprocess, sys, pathlib, datetime
import psycopg

ORCH = pathlib.Path.home() / "wingmen" / "orchestrator"
ADCDA = pathlib.Path.home() / "wingmen" / "projects" / "cosem-adcda"
STATE = ORCH / "logs" / "adcda_port_watch.state"
WATCHED_BRANCHES = ["main"]  # add feature branches here if the operator wants them watched too
# op#7517 port-watch owner — cc-cosem-platform-1 ALONE (#62694/#62695, 2026-10-11). NEVER the
# bare base 'cc-cosem-platform' (fans out to every instance; see module docstring).
PORT_OWNER = "cc-cosem-platform-1"
GIT = "/usr/bin/git" if os.path.exists("/usr/bin/git") else "git"


def _env():
    # load .env for DATABASE_URL / GH_TOKEN
    env = dict(os.environ)
    for line in (ORCH / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip())
    return env


def _git(env, *args, check=True):
    r = subprocess.run([GIT, "-C", str(ADCDA), *args], capture_output=True, text=True, env=env, timeout=60)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed rc={r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def _owner_has_any_registration(env, agent_id: str) -> bool:
    """True iff `agent_id` has ANY agent_status row at all (any status) -- a fully vanished
    identity (zero rows) means PORT_OWNER is stale and needs reassignment, a different and
    more serious signal than a routine offline blip (bus #74354, 2026-10-23: the hardcoded
    PORT_OWNER migrated hosts then was wound down entirely, and this watcher kept posting
    'assess new commits' into the void every ~3h with no live-check at all). Fail-SAFE: a DB
    error assumes True (never silently suppress a real notification over a transient read
    failure) -- same posture as this module's own dead-man's-switch philosophy elsewhere."""
    try:
        dsn = env.get("DATABASE_URL")
        with psycopg.connect(dsn) as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM agent_status WHERE agent_id=%s LIMIT 1", [agent_id])
            return cur.fetchone() is not None
    except Exception:
        return True


def _post_bus(env, to_agent, subject, body, priority="P2"):
    dsn = env.get("DATABASE_URL")
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','orch-console',true)")
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response) "
            "VALUES ('orch-console',%s,'update',%s,%s,%s,false) RETURNING id",
            (to_agent, subject, body, priority))
        rid = cur.fetchone()[0]
        c.commit()
    return rid


def main():
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    env = _env()
    STATE.parent.mkdir(exist_ok=True)
    if not ADCDA.exists():
        # dead-man's switch: can't see the repo -> say so
        print(f"[adcda_watch] DEGRADE {ts}: ADCDA repo not found at {ADCDA}", file=sys.stderr)
        try:
            _post_bus(env, "orch-console", "adcda_port_watch DEGRADED — repo not found",
                      f"Cannot monitor the ADCDA app for port-worthy features: {ADCDA} is missing. The watcher is BLIND, not reporting 'no changes'. Fix the checkout.", "P1")
        except Exception:
            pass
        sys.exit(2)

    try:
        _git(env, "fetch", "origin", "--quiet")
    except Exception as e:
        print(f"[adcda_watch] DEGRADE {ts}: fetch failed: {e}", file=sys.stderr)
        _post_bus(env, "orch-console", "adcda_port_watch DEGRADED — fetch failed",
                  f"Could not fetch origin for the ADCDA app; the watcher is BLIND this cycle (NOT 'no changes'). {e}", "P1")
        sys.exit(2)

    # read last-seen per branch
    seen = {}
    if STATE.exists():
        for line in STATE.read_text().splitlines():
            if " " in line:
                b, s = line.split(" ", 1)
                seen[b] = s.strip()

    new_lines, updated = [], dict(seen)
    for br in WATCHED_BRANCHES:
        try:
            head = _git(env, "rev-parse", f"origin/{br}")
        except Exception:
            continue
        last = seen.get(br)
        updated[br] = head
        if last and last != head:
            log = _git(env, "log", "--oneline", "--format=%h %ci %s", f"{last}..origin/{br}", check=False)
            if log:
                new_lines.append(f"origin/{br}  ({last[:8]}..{head[:8]}):\n{log}")
        elif not last:
            new_lines.append(f"origin/{br} BASELINE set at {head[:8]} — no prior state, future commits will be flagged.")

    # write state
    STATE.write_text("\n".join(f"{b} {s}" for b, s in updated.items()) + "\n")

    if not new_lines:
        print(f"[adcda_watch] {ts}: no new commits on {WATCHED_BRANCHES}")
        return

    if not _owner_has_any_registration(env, PORT_OWNER):
        degrade_body = (f"op#7517 port-watch owner {PORT_OWNER} has ZERO agent_status rows "
                        f"(fully de-registered, not just offline) -- the hardcoded PORT_OWNER "
                        f"in adcda_port_watch.py is stale and needs reassignment to whichever "
                        f"lane currently owns the ADCDA port-watch mandate. Detected commits "
                        f"are NOT lost (held in watcher state, will be reported once "
                        f"reassigned):\n\n" + "\n\n".join(new_lines))
        rid = _post_bus(env, "orch-console",
                       "adcda_port_watch DEGRADED — PORT_OWNER de-registered, reassign",
                       degrade_body, "P2")
        print(f"[adcda_watch] {ts}: PORT_OWNER {PORT_OWNER} de-registered, DEGRADE posted as #{rid}")
        return

    body = ("The operator is actively tweaking the LIVE ADCDA app (op#7517: monitor it for features to port). "
            "New commits detected since last check — assess each for PORT-WORTHINESS against your gap inventory, "
            "add the ones that belong in the platform to the port backlog, and report which to orch-console. "
            "This is DETECTION; the port-fit judgement is yours.\n\n" + "\n\n".join(new_lines))
    rid = _post_bus(env, PORT_OWNER, "ADCDA app changed — assess new commits for porting", body)
    print(f"[adcda_watch] {ts}: posted {len(new_lines)} branch-delta(s) to {PORT_OWNER} as #{rid}")


if __name__ == "__main__":
    main()
