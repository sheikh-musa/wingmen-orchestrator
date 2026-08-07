#!/usr/bin/env python3
"""adcda_port_watch.py — monitor the LIVE ADCDA app repo for new features that
need porting into the cosem platform (operator directive op#7517, 2026-07-26).

The operator actively tweaks ~/wingmen/projects/cosem-adcda (github sheikh-musa/
cosem-adcda). This watcher fetches origin, and on new commits to WATCHED_BRANCHES
beyond the last-seen SHA, posts an attributable bus row to cc-cosem-platform (the
port lane) listing the new commits for port-assessment, and nudges orch-console.

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

ORCH = pathlib.Path.home() / "wingmen" / "orchestrator"
ADCDA = pathlib.Path.home() / "wingmen" / "projects" / "cosem-adcda"
STATE = ORCH / "logs" / "adcda_port_watch.state"
WATCHED_BRANCHES = ["main"]  # add feature branches here if the operator wants them watched too
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


def _post_bus(env, to_agent, subject, body, priority="P2"):
    import psycopg
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

    body = ("The operator is actively tweaking the LIVE ADCDA app (op#7517: monitor it for features to port). "
            "New commits detected since last check — assess each for PORT-WORTHINESS against your gap inventory, "
            "add the ones that belong in the platform to the port backlog, and report which to orch-console. "
            "This is DETECTION; the port-fit judgement is yours.\n\n" + "\n\n".join(new_lines))
    rid = _post_bus(env, "cc-cosem-platform", "ADCDA app changed — assess new commits for porting", body)
    print(f"[adcda_watch] {ts}: posted {len(new_lines)} branch-delta(s) to cc-cosem-platform as #{rid}")


if __name__ == "__main__":
    main()
