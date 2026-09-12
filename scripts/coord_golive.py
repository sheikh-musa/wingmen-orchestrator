#!/usr/bin/env python3
"""coord_golive.py — gzb side of the atomic irsyad-coord Mini->gzb cutover.

Console-approved (bus 39185). This is STEP 3+4 of the runbook migration-completion
gate — run it ONLY after Nazim has done STEP 2 on the Mini: stopped the coord tmux
AND stopped the `dev.wingmen.irsyad-ingest` launchd (frees IRSYAD_SUPPORT_BOT_TOKEN;
Telegram allows exactly ONE long-poller per bot token).

Default is --dry-run: prints every pre-check + what it WOULD do, changes NOTHING.
Pass --execute to actually flip bot_channels.enabled=True + start the gzb coord unit,
but ONLY if the one-poller interlock proves the Mini poller is already stopped.

Pre-checks (hard gate for --execute):
  1. Mini poller STOPPED: ingest_poll_health(gazzabyte-irsyad).host='Sheikhs-Mini'
     AND last_ok_at is STALE (> --mini-stale-secs). A fresh Mini beat => REFUSE
     (enabling gzb now would double-poll the token -> Telegram 409 conflict).
  2. coord NOT alive on the Mini: no cc-irsyad-coord* agent_status working@Sheikhs-Mini
     within the heartbeat window.
  3. gzb readiness: IRSYAD_SUPPORT_BOT_TOKEN in shared tmpfs .env; session->musa2
     token resolves; coord worktree present; the systemd unit is installed.

Execute:
  - UPDATE bot_channels SET enabled=true WHERE channel_key='gazzabyte-irsyad'
  - sudo -n systemctl start wingmen-irsyad-coord

Post-verify (always, after execute):
  - gzb ingest now polling: poll_health row flips to host='gzbai', fresh.
  - coord lane alive on gzb: agent_status cc-irsyad-coord* working@gzbai.
  NB the CLIENT round-trip (real msg in -> coord answers out) is confirmed OUT OF BAND
  via the client's natural next message or a Nazim-run controlled test — this script
  NEVER probes the live gazzabyte group.

Rollback (--rollback): enabled=false + stop the gzb unit (then Nazim restarts the
Mini launchd). Use if post-verify fails.
"""
import argparse, os, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nervous_system.nazim_bus_notify import _dsn  # substrate DSN
import psycopg

CHANNEL = "gazzabyte-irsyad"
UNIT = "wingmen-irsyad-coord"
SHARED_ENV = "/dev/shm/wingmen-secrets/.env"
COORD_WT = os.path.expanduser("~/wingmen/projects/ihsanos-irsyad.wt-coord")
ORCH_DIR = os.path.expanduser("~/wingmen/orchestrator")

def _p(ok, msg):
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return ok

def gzb_readiness():
    print("gzb readiness:")
    ok = True
    tok = False
    try:
        with open(SHARED_ENV) as f:
            tok = any(l.startswith("IRSYAD_SUPPORT_BOT_TOKEN=") for l in f)
    except Exception as e:
        print(f"    (env read err: {e})")
    ok &= _p(tok, f"IRSYAD_SUPPORT_BOT_TOKEN present in {SHARED_ENV}")
    ok &= _p(os.path.isdir(COORD_WT), f"coord worktree present: {COORD_WT}")
    # musa2 token resolves from the session name
    try:
        r = subprocess.run([f"{ORCH_DIR}/.venv/bin/python3", "-m",
                            "scripts.lib.lane_token_resolver", "--session", "irsyad-coord"],
                           cwd=ORCH_DIR, capture_output=True, text=True, timeout=20)
        ptr = r.stdout.strip()
        ok &= _p("musa2" in ptr and os.path.isfile(ptr),
                 f"session irsyad-coord -> {ptr or '(empty)'}")
    except Exception as e:
        ok &= _p(False, f"token resolver err: {e}")
    # unit installed?
    u = subprocess.run(["systemctl", "list-unit-files", f"{UNIT}.service"],
                       capture_output=True, text=True)
    ok &= _p(f"{UNIT}.service" in u.stdout,
             f"{UNIT}.service installed (Nazim/root step if FAIL)")
    return ok

def interlock(cur, mini_stale):
    print("one-poller interlock (Mini must be STOPPED before gzb enables):")
    ok = True
    cur.execute("""SELECT host, last_ok_at, extract(epoch from now()-last_ok_at)
                   FROM ingest_poll_health WHERE channel_key=%s""", (CHANNEL,))
    row = cur.fetchone()
    if not row:
        print("    (no poll_health row — channel never polled here; treat as Mini-idle)")
        ok &= _p(True, "no live Mini poll_health for channel")
    else:
        host, last_ok, age = row
        age = float(age or 1e9)
        mini_stopped = (host != "Sheikhs-Mini") or (age > mini_stale)
        ok &= _p(mini_stopped,
                 f"Mini poller stale: host={host} last_ok_age={age:.0f}s (need >{mini_stale}s if host=Mini)")
    # coord not alive on Mini
    cur.execute("""SELECT agent_id, host, extract(epoch from now()-updated_at)
                   FROM agent_status
                   WHERE (agent_id ILIKE '%%coord%%' OR tmux_session='irsyad-coord')
                     AND host='Sheikhs-Mini' AND status='working'
                     AND updated_at > now()-interval '300 seconds'""")
    live = cur.fetchall()
    ok &= _p(not live, f"no coord lane working@Sheikhs-Mini (found {len(live)})")
    return ok

def post_verify(cur):
    print("post-verify (gzb now owns the channel + lane):")
    time.sleep(8)  # let the gzb ingest cycle + the lane heartbeat
    cur.execute("""SELECT host, extract(epoch from now()-last_ok_at)
                   FROM ingest_poll_health WHERE channel_key=%s""", (CHANNEL,))
    r = cur.fetchone()
    _p(bool(r) and r[0] == "gzbai" and float(r[1] or 1e9) < 120,
       f"gzb ingest polling channel: {r}")
    cur.execute("""SELECT agent_id, host, status, extract(epoch from now()-updated_at)
                   FROM agent_status WHERE (agent_id ILIKE '%%coord%%' OR tmux_session='irsyad-coord')
                     AND host='gzbai' ORDER BY updated_at DESC LIMIT 1""")
    r = cur.fetchone()
    _p(bool(r) and r[2] == "working", f"coord lane on gzb: {r}")
    print("  NB confirm the CLIENT round-trip out-of-band (natural reply / Nazim test) — this script never probes the group.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually flip enable + start unit (else dry-run)")
    ap.add_argument("--rollback", action="store_true", help="enable=false + stop the gzb unit")
    ap.add_argument("--mini-stale-secs", type=int, default=60)
    a = ap.parse_args()

    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        if a.rollback:
            print("ROLLBACK: enabled=false + stop gzb unit")
            cur.execute("UPDATE bot_channels SET enabled=false WHERE channel_key=%s", (CHANNEL,))
            c.commit()
            subprocess.run(["sudo", "-n", "systemctl", "stop", f"{UNIT}.service"])
            print("  done — Nazim: restart the Mini dev.wingmen.irsyad-ingest launchd.")
            return

        ready = gzb_readiness()
        lock = interlock(cur, a.mini_stale_secs)
        print(f"\nGATE: gzb-ready={ready}  interlock={lock}  -> {'CLEARED' if (ready and lock) else 'BLOCKED'}")

        if not a.execute:
            print("\n(dry-run — nothing changed. Re-run with --execute once the gate is CLEARED"
                  " AND Nazim has stopped the Mini launchd + you have VPN + coord is idle.)")
            return
        if not (ready and lock):
            print("\nREFUSING --execute: gate not cleared (would risk a double-poller / split). Fix + retry.")
            sys.exit(2)

        print("\nEXECUTE: enable channel on gzb + start coord unit")
        cur.execute("UPDATE bot_channels SET enabled=true WHERE channel_key=%s", (CHANNEL,))
        c.commit()
        print(f"  bot_channels.{CHANNEL}.enabled=true committed")
        r = subprocess.run(["sudo", "-n", "systemctl", "start", f"{UNIT}.service"],
                          capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [WARN] systemctl start failed (rc={r.returncode}): {r.stderr.strip()}"
                  f"\n  (unit may not be in the sudoers allowlist yet — Nazim adds it at install.)")
        else:
            print(f"  systemctl start {UNIT} ok")
        post_verify(cur)

if __name__ == "__main__":
    main()
