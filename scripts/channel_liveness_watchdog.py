#!/usr/bin/env python3
"""channel_liveness_watchdog.py — rung-1 reader for the channel-liveness watchdog (CAI-501).

Reads the STEP-0 poll-health signal (ingest_poll_health, migration 061) and ALERTS when a
Telegram getUpdates poll is WEDGED while the ingest process stays alive — the exact failure that
hid gazzabyte-irsyad for ~11h on 2026-09-09 (531x [Errno 54] conn-reset, process up, config fine,
only Musa caught it). It keys on POLL HEALTH, never on process-liveness and never on
bot_channels.updated_at (which advances only on a non-empty poll).

TRIGGERS (both evaluated ONLY for a channel the daemon is ACTIVELY polling — a FRESH
ingest_poll_health.updated_at; a frozen row is a different failure, see row_frozen below):
  * error_storm  — consec_errors >= N (start 6): a wedged socket, fires fastest (~30s at the 5s
                   ingest backoff). Would have fired within ~30s on the irsyad incident.
  * stale_poll   — last_ok_at older than X min (start 8): no successful poll in the window while
                   the daemon keeps trying = a wedged/dead poll. Would have fired at ~8min.

row_frozen (the row's OWN updated_at is stale > row_stale_s): the ingest daemon stopped WRITING
poll-health for this channel — either it died/hung, or the channel was deliberately disabled. We
do NOT A/B-alert on a frozen row (last_ok/consec are frozen and can't be trusted, and a disabled
channel would false-fire). It is SURFACED in the log and DEFERRED to trigger C (stranded), which
needs the enabled/pinned ownership map to tell "should be served but dark" from "correctly stopped".

GATING (charter §3 / CAI-501): DETECTION + the operator degrade-ALERT are UNGATED — a safety page
is never silenced by lease state. This rung has NO self-healing action yet (detection is the
confirmed fix; the ingest-side escalate-not-mask backoff is the hub's follow-up, and a lease-gated
kickstart executor is a separately-armed future stage). So nothing here is lease-gated. Ops-only:
own agent_id, never a governance write. Per-channel+verdict daily dedup so a persistent wedge does
not re-page every cycle. Dead-man for the dead-man: each run stamps a heartbeat so a stalled
watchdog is itself visible (CLAUDE.md §2.1).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))

# thresholds (env-overridable) — match the shape locked with the hub (38569/38580): 6 / 8min.
ERROR_STORM = int(os.environ.get("CHANLIVE_ERROR_STORM", "6"))       # consec_errors >= this -> wedged
STALE_S = int(os.environ.get("CHANLIVE_STALE_S", str(8 * 60)))       # last_ok older than this -> stale poll
ROW_STALE_S = int(os.environ.get("CHANLIVE_ROW_STALE_S", "300"))     # updated_at frozen > this -> daemon dark (defer to C)
STATE_PATH = _ORCH_DIR / "state" / "channel_liveness_watchdog.json"
SUBJECT_PREFIX = "[channel-liveness]"   # dedup anchor — keep STABLE


# ── pure decision core (unit-tested; FIRES NOTHING) ──────────────────────────
def evaluate_channel(last_ok_age_s, consec_errors, updated_age_s,
                     stale_s: int = STALE_S, error_storm: int = ERROR_STORM,
                     row_stale_s: int = ROW_STALE_S):
    """PURE. One ingest_poll_health row (ages precomputed) -> (verdict, reason).

    Args (seconds; None where the underlying timestamp is NULL):
      last_ok_age_s: age of last_ok_at (None = never had a successful poll since instrumentation)
      consec_errors: current consecutive getUpdates error count
      updated_age_s: age of updated_at (row freshness = is the daemon actively writing/polling?)

    verdict:
      "row_frozen"  — updated_age_s missing/older than row_stale_s: the daemon is NOT actively
                      writing this channel (died/hung, OR the channel was disabled). NOT A/B-alerted
                      (frozen last_ok/consec are untrustworthy; a disabled channel would false-fire)
                      — surfaced + deferred to trigger C (needs the ownership map).
      "error_storm" — daemon actively polling AND consec_errors >= error_storm. Fires fastest.
      "stale_poll"  — daemon actively polling AND a real prior success now older than stale_s.
      "ok"          — actively polling, a success within stale_s, errors below the storm bar.

    Order: row_frozen first (if the instrument is dark, its A/B inputs are frozen — don't trust
    them); then error_storm (fastest); then stale_poll; else ok. A NULL last_ok is NOT stale_poll on
    its own — a just-instrumented channel erroring from the start is caught by error_storm as consec
    climbs, never by a premature stale_poll.
    """
    if updated_age_s is None or updated_age_s > row_stale_s:
        age = "never" if updated_age_s is None else f"{updated_age_s}s"
        return "row_frozen", (f"poll-health row not fresh (updated {age} ago > {row_stale_s}s) — "
                              f"daemon not actively writing this channel; deferring to stranded-check")
    if consec_errors is not None and consec_errors >= error_storm:
        return "error_storm", (f"consec_errors={consec_errors} (>= {error_storm}) while actively "
                               f"polling — getUpdates wedged")
    if last_ok_age_s is not None and last_ok_age_s > stale_s:
        return "stale_poll", (f"no successful poll in {last_ok_age_s}s (> {stale_s}s) while actively "
                              f"polling — poll wedged/dead")
    return "ok", "healthy"


ALERT_VERDICTS = ("error_storm", "stale_poll")


# ── pure alert message ───────────────────────────────────────────────────────
def alert_message(channel_key: str, host: str, verdict: str, reason: str,
                  consec_errors=None, last_ok_age_s=None):
    """PURE (subject, body) for the operator degrade-alert. Subject prefix
    `[channel-liveness] {channel_key}@{host}: {verdict}` is the daily-dedup anchor — keep STABLE."""
    subject = f"{SUBJECT_PREFIX} {channel_key}@{host}: {verdict} — Telegram polling wedged"
    body = (
        f"TL;DR: the ingest poll for channel '{channel_key}' (host {host}) looks WEDGED — inbound "
        f"Telegram messages on it may not be arriving even though the ingest process is up.\n\n"
        f"WHAT: {reason}.\n"
        f"IMPACT: new inbound messages on this channel won't surface until polling recovers. "
        f"Offset-retention means no message is LOST within ~24h (Telegram redelivers unacked), so "
        f"this is degraded-not-lost — but a client/operator on this channel is effectively dark "
        f"until it clears.\n"
        f"WHAT TO DO: check the ingest daemon on {host} (its log + the ingest_poll_health row). If "
        f"this is an [Errno 54] conn-reset storm, a restart will NOT fix it — that's a "
        f"transport/network-path problem on {host}, not a stuck socket (the shim already re-resolves "
        f"+ tries all v4). Detect-only backstop: I did NOT touch the daemon."
    )
    return subject, body


# ── DB / state / paging shell (never touches a daemon; only detects + pages) ──
def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)  # atomic


def _fetch_rows(cur) -> list:
    """Every ingest_poll_health row with ages precomputed in SQL (so the pure core stays pure)."""
    cur.execute(
        "SELECT channel_key, host, consec_errors, "
        "       EXTRACT(EPOCH FROM (now()-last_ok_at))::bigint AS last_ok_age_s, "
        "       EXTRACT(EPOCH FROM (now()-updated_at))::bigint  AS updated_age_s "
        "FROM ingest_poll_health")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _alerted_today(cur, channel_key: str, host: str, verdict: str) -> bool:
    cur.execute(
        "SELECT 1 FROM agent_messages WHERE from_agent='cc-fleet-health' AND to_agent='orch-console' "
        "  AND subject LIKE %s AND created_at >= date_trunc('day', now()) LIMIT 1",
        [f"{SUBJECT_PREFIX} {channel_key}@{host}: {verdict}%"])
    return cur.fetchone() is not None


def _alert(cur, conn, row: dict, verdict: str, reason: str, dry: bool) -> str:
    ck, host = row["channel_key"], row["host"]
    subject, body = alert_message(ck, host, verdict, reason,
                                  row.get("consec_errors"), row.get("last_ok_age_s"))
    if dry:
        print(f"    WOULD-ALERT orch-console: {ck}@{host} {verdict}")
        return "would-alert"
    if _alerted_today(cur, ck, host, verdict):
        print(f"    already alerted {ck}@{host}/{verdict} today — deduped")
        return "deduped"
    cur.execute(
        "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,"
        " requires_response,created_at) VALUES "
        "('cc-fleet-health','orch-console','blocker',%s,%s,'P1',false, now())", [subject, body])
    conn.commit()
    print(f"    ALERTED orch-console: {ck}@{host} {verdict}")
    return "alerted"


def run(conn, dry: bool = False) -> int:
    import time
    with conn.cursor() as cur:
        rows = _fetch_rows(cur)
    state = _load_state()
    alerted = frozen = ok = 0
    print(f"channel-liveness-watchdog — {'DRY-RUN' if dry else 'LIVE'} — "
          f"storm>={ERROR_STORM} stale>{STALE_S // 60}m rowstale>{ROW_STALE_S // 60}m — {len(rows)} channel(s)")
    with conn.cursor() as cur:
        for r in sorted(rows, key=lambda x: (x["host"], x["channel_key"])):
            v, reason = evaluate_channel(r.get("last_ok_age_s"), r.get("consec_errors"),
                                         r.get("updated_age_s"))
            tag = f"{r['channel_key']}@{r['host']}"
            print(f"  {tag:34s} {v:12s} (consec={r.get('consec_errors')} "
                  f"last_ok_age={r.get('last_ok_age_s')}s upd_age={r.get('updated_age_s')}s)")
            if v == "row_frozen":
                frozen += 1
                print(f"    LOG (surfaced, not paged): {reason} — trigger-C (stranded) territory")
            elif v in ALERT_VERDICTS:
                if _alert(cur, conn, r, v, reason, dry) == "alerted":
                    alerted += 1
            else:
                ok += 1
    state["_heartbeat_epoch"] = time.time()
    if not dry:
        _save_state(state)
    print(f"done — {ok} ok, {alerted} alerted, {frozen} row-frozen(deferred), "
          f"state {'unchanged (dry-run)' if dry else 'saved'}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="channel-liveness watchdog rung-1 (detect + alert only)")
    ap.add_argument("--dry-run", action="store_true", help="detect + log only; no alert, no state write")
    args = ap.parse_args()
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(_ORCH_DIR / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_URL", file=sys.stderr)
        return 2
    conn = psycopg.connect(dsn)
    try:
        return run(conn, dry=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
