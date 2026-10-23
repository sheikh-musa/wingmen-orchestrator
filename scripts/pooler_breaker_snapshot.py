#!/usr/bin/env python3
"""pooler_breaker_snapshot — catch the substrate-pooler ECIRCUITBREAKER hammer IN THE ACT.

Incidents #45495/#45978: the pooler trips "too many authentication failures" episodically
after a DB-password rotation; a SHORT-LIVED process makes a burst of auth-failing
connections then exits, so point-in-time process/fingerprint scans miss it. And a plain
DATABASE_URL fingerprint-vs-.env check FALSE-POSITIVES, because the fleet runs several
VALID DSN string-variants (e.g. nazim-ingest) that authenticate fine — fp-drift is not
stale-credential.

This detector triggers ONLY on a REAL trip (no valid-variant false positives): every 60s
it test-connects with the .env DSN to read the breaker state. On a FRESH trip (clear->tripped
transition) it immediately snapshots every pooler connection on BOTH hosts (pid+cmd+state,
several rapid samples to catch the retrying hammer) and PAGES the SRE with the caught set —
the process(es) hammering during the trip window are the culprit. State file dedups so one
trip pages once. Dead-man: an unexpected error PAGES loudly.

Dead-man rework (held_commitment #315): the page itself goes over the SAME pooler that just
tripped, so a page that only does `INSERT INTO agent_messages` can silently lose the exact
alert this script exists to deliver. _page() now:
  1. SAVE-BEFORE-PAGE — writes the snapshot/alert to a local timestamped file FIRST, so the
     forensic catch survives even if every delivery path below fails.
  2. tries the normal DB bus insert.
  3. on DB failure — SPOOLs the alert to a durable local queue (PBS_SPOOL) and immediately
     falls back to a BOT-API flush via `scripts/nazim_send.sh` (dependency-free, times out,
     never raises) so a human sees it even with the DB down.
Every run also attempts to REPLAY the spool (retry the DB insert for anything still pending)
and PURGE entries that land — so a recovered DB drains the backlog instead of leaving it
stuck behind one failed delivery.
"""
import os, sys, json, re, subprocess, pathlib, time
from datetime import datetime, timezone

STATE = pathlib.Path(os.environ.get(
    "PBS_STATE", os.path.expanduser("~/.wingmen/pooler_breaker_snapshot.state.json")))
SPOOL = pathlib.Path(os.environ.get(
    "PBS_SPOOL", os.path.expanduser("~/.wingmen/pooler_breaker_snapshot.spool.json")))
ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_LOG_DIR = pathlib.Path(os.environ.get(
    "PBS_ALERT_LOG_DIR", os.path.join(ORCH_DIR, "logs", "pooler_breaker_snapshot_alerts")))
POOLER_IPS = ["52.62.122.103", "52.65.247.42"]


def _dsn():
    v = os.environ.get("DATABASE_URL")
    if v:
        return v
    for line in open(os.path.join(ORCH_DIR, ".env")):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no DATABASE_URL")


def _load():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(d):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=2))


def _breaker_state(dsn):
    """Return 'clear', 'tripped', or 'error:<msg>' by test-connecting."""
    import psycopg
    try:
        c = psycopg.connect(dsn, connect_timeout=10)
        c.cursor().execute("select 1")
        c.close()
        return "clear"
    except Exception as e:
        s = str(e)
        if "ECIRCUITBREAKER" in s or "too many authentication" in s:
            return "tripped"
        return "error:" + s[:100]


GZB_SNAP = r'''
import subprocess,re,time,hashlib
IPS=["52.62.122.103","52.65.247.42"]
seen={}
for _ in range(20):
    out=subprocess.run(["ss","-tnp"],capture_output=True,text=True).stdout
    for ln in out.splitlines():
        if ":5432" in ln and any(ip in ln for ip in IPS):
            m=re.search(r"pid=([0-9]+)",ln); st=ln.split()[0]
            if m:
                pid=m.group(1)
                try: cmd=open("/proc/%s/cmdline"%pid,"rb").read().replace(b"\x00",b" ").decode(errors="replace")[:70]
                except: cmd="?"
                seen.setdefault(pid,{"cmd":cmd,"st":set()})["st"].add(st)
    time.sleep(0.4)
for pid,d in seen.items():
    print("gzb pid=%s states=%s cmd=%s"%(pid,",".join(sorted(d["st"])),d["cmd"]))
'''


def _snapshot():
    lines = []
    # gzb
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "gzb", "python3 - <<'PY'\n" + GZB_SNAP + "\nPY"],
                           capture_output=True, text=True, timeout=40)
        lines += [l for l in r.stdout.splitlines() if l.startswith("gzb ")]
    except Exception as e:
        lines.append("gzb snapshot error: %s" % e)
    # mini
    try:
        seen = {}
        for _ in range(15):
            out = subprocess.run(["lsof", "-nP", "-iTCP:5432"], capture_output=True, text=True, timeout=15).stdout
            for ln in out.splitlines():
                if any(ip in ln for ip in POOLER_IPS):
                    p = ln.split()
                    if len(p) > 1 and p[1].isdigit():
                        seen.setdefault(p[1], p[0])
            time.sleep(0.4)
        for pid, comm in seen.items():
            lines.append("mini pid=%s cmd=%s" % (pid, comm))
    except Exception as e:
        lines.append("mini snapshot error: %s" % e)
    return lines


def _recent_jobs():
    """Scheduled jobs that fired in the prior ~5 min (a short-lived burst is most likely
    a scheduled job — console #45998). Mini: recently-written launchd logs. gzb: cron +
    recently-touched tick logs. Read-only."""
    out = []
    try:
        r = subprocess.run(["bash", "-c",
            "find /Users/sheikhmusa/wingmen/orchestrator/logs -name '*.log' -mmin -5 2>/dev/null | xargs -I{} basename {} | head -20"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        if r:
            out.append("mini launchd logs touched <5min: " + ", ".join(r.split()))
    except Exception as e:
        out.append("mini job scan error: %s" % e)
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "gzb", "bash -lc "
            "'crontab -l 2>/dev/null | grep -vE \"^#|^$\"; echo ---; "
            "find /home/gazzai -maxdepth 2 -name \"*.log\" -mmin -5 2>/dev/null | xargs -I{} basename {} 2>/dev/null | head -20'"],
            capture_output=True, text=True, timeout=25).stdout.strip()
        if r:
            out.append("gzb cron + logs touched <5min:\n" + r)
    except Exception as e:
        out.append("gzb job scan error: %s" % e)
    return out


def _load_spool():
    try:
        return json.loads(SPOOL.read_text())
    except Exception:
        return []


def _save_spool(entries):
    SPOOL.parent.mkdir(parents=True, exist_ok=True)
    SPOOL.write_text(json.dumps(entries, indent=2))


def _save_alert_to_disk(subject, body):
    """SAVE-BEFORE-PAGE: persist the alert to disk before attempting any delivery, so the
    forensic catch is never lost even if both the DB insert and the bot-API fallback fail."""
    try:
        ALERT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = ALERT_LOG_DIR / f"{ts}.json"
        path.write_text(json.dumps({"subject": subject, "body": body, "saved_at": ts}, indent=2))
    except Exception as e:  # noqa: BLE001 — a failed save must never block the page attempt
        print(f"[pbs] WARNING: could not save-before-page: {e}", file=sys.stderr)


def _db_insert(subject, body):
    """One attempt at the normal bus page. Raises on failure (caller decides what to do)."""
    import psycopg
    with psycopg.connect(_dsn(), connect_timeout=15) as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute("INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response,created_at)"
                    " VALUES ('cc-fleet-health','cc-fleet-health','update',%s,%s,'P1',true,now())",
                    (subject[:180], body))
        c.commit()


def _bot_fallback(subject, body):
    """BOT-API FLUSH: dependency-free dead-man page via the SRE's sanctioned Telegram path
    (same pattern as context_health_watchdog.py's _page_loud — nazim_send.sh, never the hub's
    tg_send.sh/@wingmennorchbot). Times out, never raises — a failed fallback must never crash
    the detector."""
    try:
        subprocess.run(
            [os.path.join(ORCH_DIR, "scripts", "nazim_send.sh"), f"[pooler-breaker DB-DOWN] {subject}\n\n{body}"],
            timeout=30, cwd=ORCH_DIR)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[pbs] bot-API fallback failed: {e}", file=sys.stderr)
        return False


def _flush_spool():
    """REPLAY-THEN-PURGE: retry the DB insert for every spooled alert; drop any that land.
    Runs on every invocation so a recovered DB drains the backlog without a human having to
    intervene."""
    entries = _load_spool()
    if not entries:
        return
    remaining = []
    for e in entries:
        try:
            _db_insert(e["subject"], e["body"])
            print(f"[pbs] spool flush: delivered {e.get('saved_at', '?')} to the bus")
        except Exception:
            remaining.append(e)
    _save_spool(remaining)


def _page(subject, body):
    """SAVE-BEFORE-PAGE, then DB bus; on DB failure SPOOL + BOT-API FLUSH so the alert still
    reaches a human. Never raises — a page failure must never crash the detector (the prior
    behaviour: an unhandled psycopg exception here killed main() before the state file was
    even written, so the run looked like a silent no-op in the logs)."""
    _save_alert_to_disk(subject, body)
    try:
        _db_insert(subject, body)
    except Exception as e:
        print(f"[pbs] DB page failed ({e}) — spooling + bot-API fallback", file=sys.stderr)
        entries = _load_spool()
        entries.append({"subject": subject, "body": body,
                         "saved_at": datetime.now(timezone.utc).isoformat()})
        _save_spool(entries)
        _bot_fallback(subject, body)


def main():
    st = _load()
    dsn = _dsn()
    state = _breaker_state(dsn)
    prev = st.get("state", "clear")
    print("[pbs] breaker=%s (prev=%s)" % (state, prev))

    if state != "tripped":
        # replay-then-purge: a recovered DB drains any backlog from a prior outage. Skipped
        # while still tripped — every spooled entry would just fail the same way again, and
        # each attempt pays a 15s connect timeout.
        _flush_spool()

    if state == "tripped" and prev != "tripped":
        # FRESH trip -> catch it
        snap = _snapshot()
        jobs = _recent_jobs()
        body = ("Substrate pooler ECIRCUITBREAKER is TRIPPED right now. Snapshot of every process "
                "connecting to the pooler during the trip window (the retrying old-credential client "
                "is the hammer — check which pid's DSN fails auth):\n- " + "\n- ".join(snap or ["(no connections captured)"])
                + "\n\nScheduled jobs that fired in the prior ~5 min (a bursty hammer is likely one of these):\n- "
                + "\n- ".join(jobs or ["(none)"])
                + "\n\nNOTE: the known signature is auth-fail as BARE user 'postgres' (not the tenant "
                "postgres.tscuymavysscrvoberrr). Pull the supavisor log for the exact window: "
                "GET /v1/projects/<ref>/analytics/endpoints/logs, table `logs`, cols id/timestamp/event_message.")
        _page("pooler-breaker TRIPPED — live hammer snapshot captured", body)
        print("[pbs] FRESH TRIP — snapshot paged (%d rows)" % len(snap))
    elif state.startswith("error:"):
        if st.get("last_err") != state:
            _page("pooler-breaker-snapshot DEAD-MAN: probe error", "The breaker probe hit an unexpected error (not a clean clear/tripped): " + state[6:])
            st["last_err"] = state
    else:
        st["last_err"] = None

    st["state"] = "tripped" if state == "tripped" else ("clear" if state == "clear" else prev)
    _save(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
