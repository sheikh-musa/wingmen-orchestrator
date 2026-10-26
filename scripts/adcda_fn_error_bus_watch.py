#!/usr/bin/env python3
"""adcda_fn_error_bus_watch.py — cc-fleet-health fleet-bus mirror for ADCDA Cloud Functions errors (op#28966).

Musa wanted the ADCDA detection layer on TWO surfaces: (1) his email (GCP Monitoring
alert policies A+B -> email channel, already live) AND (2) the FLEET BUS, so the fleet
(SRE/console) sees every alert and can act — "never sits silent like Hassan's login bug".

This is surface (2): a no-grant Mini poller that reads the SAME Cloud-Logging error
signal GCP alerts on (logging.googleapis.com/log_entry_count, severity=ERROR,
resource.type=cloud_run_revision) via the Monitoring API with the cosem-sa key
(which already has metric READ — no pubsub/webhook/public-endpoint needed), applies
the same thresholds, and posts ONE bus row to orch-console on a NEW breach. It is
independent of GCP's notification channels (defence-in-depth: the email path and this
path fail independently).

THRESHOLDS (mirror policies A/B; real baseline = log_entry_count ERROR on
cloud_run_revision ~33/24h ~= 0.23/10min):
  A  overall function ERROR  > OVERALL_THRESH (20) per 10min  -> P2 bus row
  B  any login/onboarding fn > PERFN_THRESH  (10) per 10min  -> P1 bus row (Hassan-class)

DEDUP (state file): fire ONCE per breach — on a clear->breach transition only, never
every tick while it stays breached. A breach that clears is pruned so a re-breach
re-fires. (Mirrors the sentry-watch page-once discipline.)

FAIL-SOFT / DEAD-MAN (charter #1): any error obtaining the SA token or querying the
metric prints LOUD and exits NON-ZERO WITHOUT advancing state — a poll that cannot run
must never look clean. A silent alert path is the one unacceptable outcome.

DEPLOYS INERT: sends/mutates state only when FLEET_FN_ERROR_WATCH_ENABLED=1 (else it
scans + logs WOULD-* and touches nothing). Its launchd job ships GATED until armed.

Usage:
  python3 scripts/adcda_fn_error_bus_watch.py --selftest   # prove decide() logic, no network
  python3 scripts/adcda_fn_error_bus_watch.py --dry-run    # poll live + decide, print, no send/state
  python3 scripts/adcda_fn_error_bus_watch.py --json       # machine-readable (poll + decide)
  python3 scripts/adcda_fn_error_bus_watch.py              # live (only sends when ENABLED)
Env: DATABASE_URL (bus send); GCP_SA_KEY (default ~/.wingmen/keys/cosem-sa.json); FLEET_FN_ERROR_WATCH_ENABLED.
"""
from __future__ import annotations

import os
import sys
import json
import subprocess
from pathlib import Path

_ORCH = Path(__file__).resolve().parent.parent

# ── tunables ─────────────────────────────────────────────────────────────────
PROJECT = os.environ.get("ADCDA_PROJECT", "cosem-adcda-cb6d9")
OVERALL_THRESH = int(os.environ.get("FN_ERR_OVERALL_THRESH", "20"))   # errors/10min, all fns
PERFN_THRESH = int(os.environ.get("FN_ERR_PERFN_THRESH", "10"))        # errors/10min, one login fn
ALIGN_SEC = 600            # 10-min buckets (matches policies A/B)
WINDOW_MIN = 25            # look back far enough to see the last ~2 complete buckets
SA_KEY = os.environ.get("GCP_SA_KEY", os.path.expanduser("~/.wingmen/keys/cosem-sa.json"))
STATE_PATH = _ORCH / "state" / "adcda_fn_error_bus_watch.json"
SURFACE_PREFIX = "[adcda-fn-error-watch]"

# login/onboarding cloud_run service_names (all lowercase; the Hassan-class set — mirrors policy B)
LOGIN_FNS = {
    "authorizephonesignin", "authorizetraineeidentitylogin", "unlocktraineelogin",
    "completetrainerselfonboarding", "scanemiratesid", "upsertonboardingphoneallowlist",
    "passkeyauthenticationchallenge", "passkeyauthenticationverify",
    "oncourserosteronboarded", "ontraineecreate",
}


# REFIRE DEBOUNCE (orch-console bus #76716, 2026-10-26): the Monitoring API query takes
# max() over a WINDOW_MIN=25min lookback, so a resolved spike's own bucket can still be the
# max for as long as it stays in that window -- and GCP's own alert evaluation lag means a
# LATE-arriving poll can see state already cleared (by an earlier, luckier poll) and then
# re-see that SAME stale bucket moments later, reading as a brand-new clear->breach
# transition. Observed live: #76675 (07:26, overall=22) and #76712 (07:52, overall=22,
# IDENTICAL count, window orch-console traced to one that ended 07:38) both fired, 26min
# apart, for what was really one incident. Suppress a re-fire of the SAME key within
# REFIRE_COOLDOWN_S of its last fire IF the new count is NOT an escalation (<= the last
# fired count) -- a stale re-surfaced reading never looks worse than what already fired, so
# this never swallows a genuinely growing Hassan-class breach (anything count > last fired
# still fires immediately, cooldown or not).
REFIRE_COOLDOWN_S = int(os.environ.get("FN_ERR_REFIRE_COOLDOWN_S", "1800"))  # 30min


# ── pure decision logic (unit-tested via --selftest; no network) ──────────────
def decide(state: dict, counts: dict, now: float = 0.0) -> dict:
    """Given prior state + current {overall:int, per_fn:{name:int}}, return the
    breaches to fire NOW (clear->breach transitions, debounced -- see REFIRE_COOLDOWN_S
    above) and the next state.

    state = {"active": [<breach_key>, ...], "last_fired": {<breach_key>: {"at": epoch,
    "count": int}}} — breaches currently firing (deduped) + when/at-what-count each was
    last actually fired. A breach_key is "overall" or "fn:<service_name>".
    """
    prev_active = set(state.get("active", []))
    last_fired = dict(state.get("last_fired", {}))  # copy: never mutate the caller's input state
    now_breached = set()
    detail = {}

    if counts.get("overall", 0) > OVERALL_THRESH:
        now_breached.add("overall")
        detail["overall"] = counts["overall"]
    for fn, n in (counts.get("per_fn") or {}).items():
        if n > PERFN_THRESH:
            k = f"fn:{fn}"
            now_breached.add(k)
            detail[k] = n

    candidates = sorted(now_breached - prev_active)   # NEW breaches only (dedup)
    fire = []
    for k in candidates:
        lf = last_fired.get(k)
        if lf and (now - lf.get("at", 0)) < REFIRE_COOLDOWN_S and detail[k] <= lf.get("count", -1):
            continue  # stale re-surface of an already-reported, non-escalating reading
        fire.append(k)
        last_fired[k] = {"at": now, "count": detail[k]}
    cleared = sorted(prev_active - now_breached)
    return {
        "fire": fire,
        "cleared": cleared,
        "detail": detail,
        "counts": counts,
        "state": {"active": sorted(now_breached), "last_fired": last_fired},
    }


# ── shell: token / metric / bus / state (not reached by the pure tests) ───────
def _die(msg: str, code: int = 1):
    """Dead-man: fail LOUD, non-zero — a poll that cannot run must never look clean."""
    print(f"{SURFACE_PREFIX} COULD NOT POLL METRIC: {msg}", file=sys.stderr)
    sys.exit(code)


def _token() -> str:
    """Obtain an access token for cosem-sa. Raises -> caller dead-mans."""
    if not os.path.exists(SA_KEY):
        raise RuntimeError(f"SA key not found: {SA_KEY}")
    env = dict(os.environ, CLOUDSDK_CORE_DISABLE_PROMPTS="1")
    subprocess.run(["gcloud", "auth", "activate-service-account", "--key-file", SA_KEY],
                   check=True, capture_output=True, env=env, timeout=60)
    out = subprocess.run(["gcloud", "auth", "print-access-token"],
                         check=True, capture_output=True, text=True, env=env, timeout=60)
    tok = out.stdout.strip()
    if not tok:
        raise RuntimeError("empty access token")
    return tok


def _query(token: str, extra_filter: str, group_by_fn: bool) -> dict:
    """Return {service_name_or_'_all_': max_bucket_count} over the recent window.
    Raises on any API failure -> caller dead-mans."""
    import urllib.request
    import urllib.parse
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    start = (now - datetime.timedelta(minutes=WINDOW_MIN)).replace(microsecond=0)
    end = now.replace(microsecond=0)
    base_filter = ('metric.type="logging.googleapis.com/log_entry_count" '
                   'AND metric.label.severity="ERROR" '
                   'AND resource.type="cloud_run_revision"')
    flt = base_filter + extra_filter
    params = [
        ("filter", flt),
        ("interval.startTime", start.isoformat().replace("+00:00", "Z")),
        ("interval.endTime", end.isoformat().replace("+00:00", "Z")),
        ("aggregation.alignmentPeriod", f"{ALIGN_SEC}s"),
        ("aggregation.perSeriesAligner", "ALIGN_SUM"),
        ("aggregation.crossSeriesReducer", "REDUCE_SUM"),
    ]
    if group_by_fn:
        params.append(("aggregation.groupByFields", "resource.label.service_name"))
    url = (f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries?"
           + urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=40) as r:  # noqa: S310 (trusted host)
        data = json.loads(r.read().decode())
    if data.get("error"):
        raise RuntimeError(f"monitoring API error: {str(data['error'])[:200]}")
    out = {}
    for s in data.get("timeSeries", []):
        name = s.get("resource", {}).get("labels", {}).get("service_name", "_all_") if group_by_fn else "_all_"
        vals = [int(p["value"].get("int64Value", 0)) for p in s.get("points", [])]
        out[name] = max(vals) if vals else 0
    return out


def _metric_counts() -> dict:
    """Poll overall + per-login-fn error counts (max recent 10min bucket). Dead-mans on failure."""
    token = _token()
    overall_map = _query(token, "", group_by_fn=False)
    overall = max(overall_map.values()) if overall_map else 0
    fn_regex = "|".join(sorted(LOGIN_FNS))
    per_fn_all = _query(token, f' AND resource.label.service_name=monitoring.regex.full_match("{fn_regex}")',
                        group_by_fn=True)
    # only keep the login set (regex already restricts, but be defensive)
    per_fn = {k: v for k, v in per_fn_all.items() if k in LOGIN_FNS}
    return {"overall": overall, "per_fn": per_fn}


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {"active": []}


def _save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)


def _pg():
    import psycopg2
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _post_bus(mtype: str, priority: str, subject: str, body: str) -> None:
    with _pg() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,requires_response) "
            "VALUES ('cc-fleet-health','orch-console',%s,%s,%s,%s,false)",
            (mtype, subject, body, priority))
        conn.commit()


def _fire_row(key: str, detail: dict) -> tuple:
    """(message_type, priority, subject, body) for a breach key."""
    if key == "overall":
        n = detail.get("overall")
        return ("blocker", "P2",
                f"{SURFACE_PREFIX} overall Cloud Functions ERROR spike ({n}/10min > {OVERALL_THRESH})",
                "TL;DR (fleet-bus mirror of GCP alert A): ADCDA Cloud Functions overall error-log "
                f"spike — {n} ERRORs in a 10-min window (baseline ~0.23/10min). Broad breakage likely. "
                "Check Cloud Logging severity>=ERROR on cosem-adcda-cb6d9 for the failing function(s). "
                "(Musa also alerted by email.)")
    fn = key.split("fn:", 1)[1]
    n = detail.get(key)
    return ("blocker", "P1",
            f"{SURFACE_PREFIX} login/onboarding fn '{fn}' erroring ({n}/10min > {PERFN_THRESH}) — Hassan-class",
            f"TL;DR (fleet-bus mirror of GCP alert B): ADCDA login/onboarding function '{fn}' is erroring "
            f"{n} times/10min — trainer login/onboarding likely broken for users (the exact silent-bug class "
            "that let Hassan's login sit broken). Check Cloud Logging for this service on cosem-adcda-cb6d9. "
            "(Musa also alerted by email.)")


def run(dry: bool = False) -> dict:
    """Poll -> decide -> (fire new breaches to bus) when ENABLED+not-dry. Dead-mans loud on any failure."""
    try:
        counts = _metric_counts()
    except Exception as e:  # noqa: BLE001 — fail-soft LOUD, never silent
        _die(f"{type(e).__name__}: {e}")
    state = _load_state()
    import time
    d = decide(state, counts, now=time.time())
    enabled = os.environ.get("FLEET_FN_ERROR_WATCH_ENABLED") == "1"
    send_live = enabled and not dry
    print(f"adcda-fn-error-bus-watch — {'LIVE' if send_live else 'SCAN+LOG'} — "
          f"overall={counts['overall']} (thr {OVERALL_THRESH}); "
          f"login-fns breaching={[k for k in d['detail'] if k.startswith('fn:')]}; "
          f"NEW breaches={d['fire']}; cleared={d['cleared']} — enabled={enabled} dry={dry}")
    for k in d["fire"]:
        print(f"  {'FIRE' if send_live else 'WOULD-FIRE'} {k} = {d['detail'].get(k)}")
    if send_live:
        for k in d["fire"]:
            mt, pr, subj, body = _fire_row(k, d["detail"])
            _post_bus(mt, pr, subj, body)
        _save_state(d["state"])   # advance state only on a real live run
    print(f"done — {'sent + state saved' if send_live else 'scan+log, no send/state'}.")
    return d


# ── selftest (pure decide() logic; no network, no DB) ─────────────────────────
def _selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'} {name}")
        ok = ok and cond

    # clean -> no fire
    d = decide({"active": []}, {"overall": 3, "per_fn": {"authorizephonesignin": 2}})
    check("below thresholds -> no fire", d["fire"] == [])
    # overall breach fires once
    d = decide({"active": []}, {"overall": 25, "per_fn": {}})
    check("overall>20 fires", d["fire"] == ["overall"])
    # same breach already active -> no re-fire (dedup)
    d = decide({"active": ["overall"]}, {"overall": 25, "per_fn": {}})
    check("overall still breached -> dedup, no re-fire", d["fire"] == [])
    # breach clears -> pruned from state, reported in cleared
    d = decide({"active": ["overall"]}, {"overall": 1, "per_fn": {}})
    check("overall cleared -> state empty", d["state"]["active"] == [])
    check("overall cleared -> cleared list", d["cleared"] == ["overall"])
    # per-login-fn breach fires once, keyed by fn
    d = decide({"active": []}, {"overall": 0, "per_fn": {"authorizephonesignin": 11, "scanemiratesid": 3}})
    check("login fn>10 fires keyed", d["fire"] == ["fn:authorizephonesignin"])
    # re-breach after clear re-fires WHEN no prior fire record exists (first-ever breach shape)
    d = decide({"active": []}, {"overall": 21, "per_fn": {}})
    check("re-breach after clear re-fires (no fire history)", d["fire"] == ["overall"])
    # boundary: exactly at threshold does NOT fire (strict >)
    d = decide({"active": []}, {"overall": 20, "per_fn": {"authorizephonesignin": 10}})
    check("exactly-at-threshold does not fire", d["fire"] == [])

    # ── REFIRE DEBOUNCE (bus #76716): stale GCP metric re-surfaces an already-reported,
    # non-escalating count shortly after a clear -> suppressed; a genuinely WORSE count,
    # or one outside the cooldown window, still fires. ──
    base_state = {"active": [], "last_fired": {"overall": {"at": 1000.0, "count": 22}}}
    d = decide(base_state, {"overall": 22, "per_fn": {}}, now=1000.0 + 60)
    check("stale re-surface, same count, inside cooldown -> suppressed", d["fire"] == [])
    d = decide(base_state, {"overall": 21, "per_fn": {}}, now=1000.0 + 60)
    check("stale re-surface, LOWER (but still breaching) count, inside cooldown -> suppressed",
          d["fire"] == [])
    d = decide(base_state, {"overall": 30, "per_fn": {}}, now=1000.0 + 60)
    check("genuinely WORSE count, inside cooldown -> still fires (never silences escalation)",
          d["fire"] == ["overall"])
    d = decide(base_state, {"overall": 22, "per_fn": {}}, now=1000.0 + REFIRE_COOLDOWN_S + 1)
    check("same count, OUTSIDE cooldown -> fires again", d["fire"] == ["overall"])
    d = decide(base_state, {"overall": 22, "per_fn": {}}, now=1000.0 + 60)
    check("suppressed fire still updates state.active (dedup bookkeeping intact)",
          d["state"]["active"] == ["overall"])
    print("SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return _selftest()
    # load .env for DATABASE_URL + arm flag on a manual run
    try:
        from dotenv import load_dotenv
        load_dotenv(_ORCH / ".env")
    except Exception:
        pass
    if "--json" in args:
        d = run(dry=True)
        print(json.dumps({"fire": d["fire"], "cleared": d["cleared"],
                          "detail": d["detail"], "counts": d["counts"]}, sort_keys=True))
        return 0
    run(dry="--dry-run" in args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
