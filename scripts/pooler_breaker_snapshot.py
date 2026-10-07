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
"""
import os, sys, json, re, subprocess, pathlib, time

STATE = pathlib.Path(os.environ.get(
    "PBS_STATE", os.path.expanduser("~/.wingmen/pooler_breaker_snapshot.state.json")))
ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def _page(subject, body):
    import psycopg
    with psycopg.connect(_dsn(), connect_timeout=15) as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute("INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response,created_at)"
                    " VALUES ('cc-fleet-health','cc-fleet-health','update',%s,%s,'P1',true,now())",
                    (subject[:180], body))
        c.commit()


def main():
    st = _load()
    dsn = _dsn()
    state = _breaker_state(dsn)
    prev = st.get("state", "clear")
    print("[pbs] breaker=%s (prev=%s)" % (state, prev))

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
