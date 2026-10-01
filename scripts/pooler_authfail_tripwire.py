#!/usr/bin/env python3
"""pooler_authfail_tripwire.py — TEMPORARY tripwire (op#24342 / orch-console #48335).

Watches the password-auth-failure rate at the Supabase pooler until the
category-(c) env-first-_dsn PR is merged + pulled (then REMOVE this + its launchd job).
Residual hammer = pre-rotation bodies making env-first DB connects with the stale DSN.

MULTI-HOST (fleet-health/tripwire-gzb): the fleet runs on TWO egress hosts — the Mini
(peer_ip 121.6.154.64) and gzb (peer_ip 66.96.212.114). The first cut was Mini-only, so
a gzb-origin auth-fail burst (e.g. gzb heartbeat loops) was INVISIBLE and blinded us
during a real breaker trip whose source was gzb. This version runs ONE tripwire that
sees BOTH hosts via the Supabase Mgmt API (read-only HTTPS, never a pooler DB connect)
and attributes fails per host by peer_ip, so a burst on EITHER host raises the tripwire.

Every run (launchd, 15 min): count "password authentication failed" events in the last
15 min from the Mgmt API postgres_logs, bucket them per host by peer_ip, compute each
host's per-minute rate, and probe the breaker. BREACH = ANY host's rate > 5/min OR
breaker tripped -> LOUD P1 to cc-fleet-health (self-wake) + orch-console naming the
breached host(s). Below threshold: log per-host rates, no action. Fail LOUD if it cannot
measure (never silently green). Detect + page only — it does NOT auto-relaunch
(destructive mass action stays with the woken SRE, per charter).
"""
import os, sys, json, subprocess, urllib.parse, urllib.request, datetime

# Per-host egress IPs at the Supabase pooler. Confirm on each host with
# `curl -s https://api.ipify.org` (Mini confirmed 121.6.154.64 2026-10-02; gzb
# 66.96.212.114 per op handoff — re-confirm on gzb and correct here if it NATs out
# a different address).
HOST_IPS = {
    "mini": "121.6.154.64",
    "gzb": "66.96.212.114",
}
REF = "tscuymavysscrvoberrr"
WINDOW_MIN = 15
RATE_THRESHOLD = 5.0  # per minute, per host
LOGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "pooler-authfail-tripwire.log")
ORCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _log(msg):
    line = "%s [tripwire] %s" % (datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), msg)
    print(line, file=sys.stderr)
    try:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _extract_peer_ip(row):
    """Pull peer_ip out of a Mgmt-API log row. It may live under `log_attributes`
    (sometimes a single-element list) or under `metadata`; be defensive about shape."""
    for key in ("log_attributes", "metadata"):
        attr = row.get(key)
        if isinstance(attr, list) and attr:
            attr = attr[0]
        if isinstance(attr, dict):
            ip = attr.get("peer_ip")
            if ip:
                return ip
    return None


def _fetch_authfail_rows():
    """Query the Supabase Mgmt API (read-only HTTPS) for auth-fail log rows in the
    window. Raises if it cannot measure — the caller fails LOUD, never silently green."""
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("SUPABASE_ACCESS_TOKEN absent — cannot measure")
    end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(minutes=WINDOW_MIN)
    sql = ("SELECT timestamp, event_message, log_attributes FROM postgres_logs "
           "WHERE event_message ILIKE '%password authentication failed%' "
           "ORDER BY timestamp DESC LIMIT 500")
    qs = urllib.parse.urlencode({
        "sql": sql,
        "iso_timestamp_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "iso_timestamp_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    url = "https://api.supabase.com/v1/projects/%s/analytics/endpoints/logs?%s" % (REF, qs)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % token})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)
    return rows.get("result", rows) if isinstance(rows, dict) else rows


def _count_authfails_by_host(rows=None):
    """Bucket auth-fail rows per known host by peer_ip. Returns a dict with a key per
    HOST_IPS host plus 'unattributed' for rows whose peer_ip matches no known host."""
    if rows is None:
        rows = _fetch_authfail_rows()
    ip_to_host = {ip: host for host, ip in HOST_IPS.items()}
    counts = {host: 0 for host in HOST_IPS}
    counts["unattributed"] = 0
    for row in rows:
        host = ip_to_host.get(_extract_peer_ip(row))
        counts[host if host else "unattributed"] += 1
    return counts


def _breaker_tripped():
    try:
        import psycopg
        dsn = os.environ.get("DATABASE_URL")
        with psycopg.connect(dsn, connect_timeout=10) as c, c.cursor() as cur:
            cur.execute("SELECT 1")
        return False
    except Exception as e:
        return "ECIRCUITBREAKER" in str(e)


def _page(subject, body):
    bus = os.path.join(ORCH_DIR, "scripts", "bus_send.py")
    py = sys.executable
    for to in ("cc-fleet-health", "orch-console"):
        try:
            subprocess.run(
                [py, bus, "--to", to, "--from", "cc-fleet-health", "--type", "blocker",
                 "--priority", "P1", "--req", "--subject", subject],
                input=body.encode(), cwd=ORCH_DIR, timeout=30, check=True)
        except Exception as e:
            _log("PAGE FAILED to %s: %s" % (to, e))


def _rates(counts):
    """Per-host per-minute rate for the known hosts (unattributed excluded from rate)."""
    return {host: counts.get(host, 0) / float(WINDOW_MIN) for host in HOST_IPS}


def _breached_hosts(rates):
    return sorted(h for h, r in rates.items() if r > RATE_THRESHOLD)


def _remediation_for(hosts):
    """Host-aware next action for the paged SRE."""
    tips = []
    if "mini" in hosts:
        tips.append("Mini: run option (a) — busy-gated same-account relaunch of the 12 "
                    "non-audit pre-rotation lanes (switch_lane_token.sh --relaunch) to clear "
                    "their stale base-env DSN; audit-cosem/substrate stay PATH-B.")
    if "gzb" in hosts:
        tips.append("gzb: a gzb-origin burst is usually heartbeat/boot loops holding the "
                    "stale DSN (op#24342 pattern) — identify the looping gzb proc(s) and "
                    "relaunch/settle them; do NOT run Mini option (a) blindly for a gzb-only "
                    "burst.")
    return " ".join(tips) if tips else "Breaker tripped with no host over threshold — inspect both hosts manually."


def main():
    try:
        counts = _count_authfails_by_host()
        rates = _rates(counts)
        tripped = _breaker_tripped()
    except Exception as e:
        # Fail LOUD, never silently green.
        _log("COULD NOT MEASURE: %s — paging" % e)
        _page("[tripwire] COULD NOT MEASURE fleet auth-fail rate (op#24342)",
              "The pooler auth-fail tripwire could not measure (%s). Treat as UNKNOWN, not safe — "
              "check manually (Mgmt API postgres_logs, peer_ip per host) and the breaker." % e)
        return 2

    breached = _breached_hosts(rates)
    breach = bool(breached) or tripped
    rate_str = " ".join("%s=%d(%.2f/min)" % (h, counts.get(h, 0), rates[h]) for h in HOST_IPS)
    _log("authfails_%dm %s unattributed=%d breaker_tripped=%s breach=%s breached_hosts=%s"
         % (WINDOW_MIN, rate_str, counts.get("unattributed", 0), tripped, breach, ",".join(breached) or "-"))

    if "--dry-run" in sys.argv:
        print("DRY-RUN: %s unattributed=%d breaker_tripped=%s breach=%s breached_hosts=%s "
              "(threshold %.0f/min per host) — no page"
              % (rate_str, counts.get("unattributed", 0), tripped, breach,
                 ",".join(breached) or "-", RATE_THRESHOLD))
        return 0

    if breach:
        who = ",".join(breached) if breached else "none(breaker-only)"
        _page("[tripwire] BREACH: pooler auth-fails on %s (op#24342) — act now" % who,
              "TRIPWIRE BREACH (op#24342 residual). Per-host password-auth failures in last %dm: %s "
              "(unattributed=%d); breaker_tripped=%s; over-threshold host(s)=%s (threshold %.0f/min). "
              "Next: %s Then confirm the rate drops."
              % (WINDOW_MIN, rate_str, counts.get("unattributed", 0), tripped,
                 ",".join(breached) or "-", RATE_THRESHOLD, _remediation_for(breached)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
