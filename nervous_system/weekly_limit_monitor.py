#!/usr/bin/env python3
"""weekly_limit_monitor.py — headless Max weekly-usage monitor (op#9658).

Motivating failure: the fleet hit 89% of the Musa Max pool with NO early warning;
the operator caught it by hand. This warns at ~75% so lanes can be moved
proactively, and pages urgently at ~90%.

PROGRAMMATIC SOURCE (researched + verified 2026-08-03): an OAuth-authenticated
`POST /v1/messages` (Authorization: Bearer <oauth token> + `anthropic-beta:
oauth-2025-04-20`) returns UNIFIED rate-limit headers that expose subscription
usage directly — no CLI usage subcommand exists, and no content parsing is needed:
    anthropic-ratelimit-unified-7d-utilization   weekly usage FRACTION (0..1)  <- the number
    anthropic-ratelimit-unified-7d-reset         epoch when the weekly window resets
    anthropic-ratelimit-unified-7d-status        allowed | allowed_warning | ...
    anthropic-ratelimit-unified-5h-utilization   5-hour window fraction
    anthropic-ratelimit-unified-representative-claim   which window currently binds
The `7d-utilization` reads 0.89 on the Musa pool — matching the operator's observed
89% — which validates the signal. Even a 429 (over limit) still carries the headers
(urllib HTTPError.headers), so we can read utilization while rate-limited.

Cost: a 1-token haiku probe per pool per run — negligible against the weekly pool.

We track TWO independent pools: Musa (CLAUDE_CODE_OAUTH_TOKEN in orchestrator .env)
and Syed (~/.wingmen/keys/syed-oauth-token).

DEAD-MAN'S-SWITCH (doctrine: a silent-fail monitor is worse than none): every run
writes a heartbeat; a probe failure PAGES loudly rather than passing silently; an
unhandled crash pages via a dependency-free path. This is ALERT-ONLY — it never
takes a fleet action, so it is ungated (a safety signal is never silenced).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ORCH_DIR))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_ORCH_DIR / ".env")

API_URL = "https://api.anthropic.com/v1/messages"
PROBE_BODY = json.dumps(
    {"model": "claude-haiku-4-5", "max_tokens": 1,
     "messages": [{"role": "user", "content": "hi"}]}).encode()

WARN = float(os.environ.get("WEEKLY_LIMIT_WARN", "0.75"))    # ~75%
URGENT = float(os.environ.get("WEEKLY_LIMIT_URGENT", "0.90"))  # ~90%

STATE_FILE = _ORCH_DIR / "logs" / "weekly_limit_monitor_state.json"
HEARTBEAT_FILE = _ORCH_DIR / "logs" / "weekly_limit_monitor_heartbeat"
LOG_FILE = _ORCH_DIR / "logs" / "weekly_limit_monitor.log"

# Pool name -> how to load its OAuth token. Value is (kind, ref).
POOLS = {
    "Musa": ("env", "CLAUDE_CODE_OAUTH_TOKEN"),
    "Syed": ("file", str(Path.home() / ".wingmen" / "keys" / "syed-oauth-token")),
}


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_token(kind: str, ref: str) -> str:
    if kind == "env":
        tok = os.environ.get(ref, "")
    else:
        tok = Path(ref).read_text().strip() if Path(ref).exists() else ""
    return tok.strip()


def probe_pool(name: str) -> dict:
    """Read the unified rate-limit headers for one pool. Raises on a genuine
    probe failure (missing token / network) so the caller can page LOUD — a
    silent miss is worse than none."""
    kind, ref = POOLS[name]
    token = _load_token(kind, ref)
    if not token:
        raise RuntimeError(f"no OAuth token for pool {name} ({kind}:{ref})")
    req = urllib.request.Request(API_URL, data=PROBE_BODY, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        headers = resp.headers
        http = resp.status
    except urllib.error.HTTPError as e:
        # Over-limit / auth errors still carry the rate-limit headers — use them.
        headers = e.headers
        http = e.code
    def _f(key, default=None):
        v = headers.get(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    u7d = _f("anthropic-ratelimit-unified-7d-utilization")
    if u7d is None:
        # A fully-IDLE/parked token (request succeeded, http 200, auth fine) simply
        # returns no fresh utilization header — nothing new to report, NOT a failure
        # (verified 2026-08-04: Musa parked at 94% dropped the header once the fleet
        # moved off it, while active Syed kept it). Signal 'no fresh data' so the
        # caller keeps the last-known reading and does NOT false-page. A genuine
        # break (auth error / API shape change) arrives at a NON-200 status — that
        # still raises so the dead-man's-switch fires.
        if http == 200:
            return {"pool": name, "http": http, "u7d": None, "idle_no_data": True}
        raise RuntimeError(
            f"pool {name}: no unified-7d-utilization header (http={http}); "
            f"auth broke or API shape changed")
    return {
        "pool": name, "http": http,
        "u7d": u7d,
        "reset7d": _f("anthropic-ratelimit-unified-7d-reset"),
        "status7d": headers.get("anthropic-ratelimit-unified-7d-status"),
        "u5h": _f("anthropic-ratelimit-unified-5h-utilization"),
        "reset5h": _f("anthropic-ratelimit-unified-5h-reset"),
        "status": headers.get("anthropic-ratelimit-unified-status"),
        "binds": headers.get("anthropic-ratelimit-unified-representative-claim"),
    }


def _level(util: float) -> str:
    if util >= URGENT:
        return "urgent"
    if util >= WARN:
        return "warn"
    return "ok"


def _fmt_reset(epoch) -> str:
    if not epoch:
        return "unknown"
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log(f"state-write-failed: {e}")


def _emit_bus(subject: str, body: str, priority: str,
              to_agent: str = "orch-console") -> None:
    """Attributable alert on the bus from cc-fleet-health — the SRE's sanctioned
    channel (the bus, NOT Telegram; the operator TG bot is the hub's pen iv, not
    ours — charter §3/§5). requires_response=false + responded_at stamped so it
    never re-creates the SLA false-stall flood NOR nags in the needs-you hero.
    Raises on failure (LOUD)."""
    import psycopg
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("no DATABASE_URL for bus alert")
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,"
            " priority,requires_response,responded_at) "
            "VALUES ('cc-fleet-health',%s,'update',%s,%s,%s,false,now())",
            (to_agent, subject[:180], body, priority))
        c.commit()


def _persist_reading(p: dict) -> None:
    """UPSERT the latest reading for one pool into public.pool_usage — the console's
    read path (op#9770). One row per pool, overwritten in place; `updated_at`
    stamps THIS reading's freshness so the console can grey out a stale one.
    Raises on failure so the caller can surface it LOUD (a silent-fail persist
    would let the console show a frozen number — the exact stale-info bug op#9770
    is closing). Uses the SERVICE_ROLE via DATABASE_URL, same as the bus alert."""
    import psycopg
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("no DATABASE_URL for pool_usage persist")
    pct7 = round(p["u7d"] * 100, 1)
    pct5 = round((p.get("u5h") or 0) * 100, 1)
    reset = p.get("reset7d")
    reset_at = datetime.fromtimestamp(reset, tz=timezone.utc) if reset else None
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO pool_usage (pool, pct_7d, pct_5h, resets_at, status_7d, updated_at) "
            "VALUES (%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (pool) DO UPDATE SET "
            "  pct_7d=EXCLUDED.pct_7d, pct_5h=EXCLUDED.pct_5h, "
            "  resets_at=EXCLUDED.resets_at, status_7d=EXCLUDED.status_7d, "
            "  updated_at=now()",
            (p["pool"], pct7, pct5, reset_at, p.get("status7d")))
        c.commit()


def _page(subject: str, body: str, priority: str, dry: bool,
          to_agent: str = "orch-console") -> None:
    if dry:
        print(f"[WOULD-PAGE {priority} -> {to_agent}] {subject}\n{body}\n")
        return
    try:
        _emit_bus(subject, body, priority, to_agent=to_agent)
        log(f"bus-alert -> {to_agent} [{priority}]: {subject}")
    except Exception as e:
        # LOUD: the alert channel itself failed. Never silent.
        log(f"ALERT-DELIVERY-FAILED ({to_agent}) — NOT notified: {e}")
        print(f"[weekly-limit] ALERT-DELIVERY-FAILED ({to_agent}): {e}", file=sys.stderr)


def _alert_body(p: dict, level: str) -> str:
    pct = round(p["u7d"] * 100)
    other = "Syed" if p["pool"] == "Musa" else "Musa"
    return (
        f"[Max weekly-limit monitor] {p['pool']} pool is at {pct}% of its WEEKLY limit "
        f"({level.upper()}).\n\n"
        f"WHAT: {p['pool']}'s Max weekly usage = {pct}% (5-hour window "
        f"{round((p.get('u5h') or 0)*100)}%). The weekly window resets "
        f"{_fmt_reset(p.get('reset7d'))}.\n"
        f"WHY: at 100% every Claude-Code lane on the {p['pool']} pool stalls at once "
        f"(the 2026-08-03 89%-with-no-warning incident). Warn is {round(WARN*100)}%, "
        f"urgent is {round(URGENT*100)}%.\n"
        f"WHAT TO DO: move lanes to the {other} pool if it has headroom, and/or flip "
        f"heavy lanes to Sonnet (separate weekly limit). If both pools are high, pace "
        f"the fleet until the weekly reset.\n"
        f"(source: anthropic-ratelimit-unified-7d-utilization; binding window: "
        f"{p.get('binds')})")


# The operator identity on the bus is 'musa' (the same target cai addresses for
# operator decisions). #14988: the operator should get this EARLY warning
# directly, not on a latency-adding hop through Nazim reading his console inbox.
OPERATOR_AGENT = "musa"


def _operator_body(p: dict, level: str) -> str:
    """ELI5, phone-first — the operator reads this on his own alert path (NOT a
    relay through Nazim). Short, plain, one clear ask."""
    pct = round(p["u7d"] * 100)
    return (
        f"⚠️ Heads up — the {p['pool']} Claude pool is at {pct}% of its WEEKLY limit "
        f"({level.upper()}).\n\n"
        f"WHAT: at 100% every Claude-Code lane on the {p['pool']} pool stops at once, "
        f"until the weekly window resets {_fmt_reset(p.get('reset7d'))}. This is the "
        f"early warning so you are not surprised (like the 89%-no-warning day).\n"
        f"WHAT TO DO: nothing needed if you're fine pacing until reset. If you want "
        f"heavy work to keep going, say so and I'll move lanes to the other pool / "
        f"flip lanes to Sonnet. Nazim has the same alert and is already acting.")


def run(dry: bool = False, as_json: bool = False) -> int:
    state = load_state()
    results = []
    failures = []
    persist_failures = []
    for name in POOLS:
        try:
            p = probe_pool(name)
        except Exception as e:
            failures.append((name, str(e)))
            log(f"PROBE-FAILED {name}: {e}")
            continue
        if p.get("u7d") is None:
            # Idle/parked token: no fresh reading this run. Keep the last-known
            # pool_usage row + prior alert state; do NOT persist or page. (A real
            # failure raised above and is handled as a probe failure below.)
            log(f"{p['pool']}: idle token (http 200, no 7d header) — keeping "
                f"last-known reading, no persist/page")
            continue
        results.append(p)
        # Persist the reading for the console (op#9770). A persist failure does
        # NOT block alerting (the primary safety function) — but it is surfaced
        # LOUD below so the console never silently shows a frozen number.
        if not dry:
            try:
                _persist_reading(p)
            except Exception as e:
                persist_failures.append((name, str(e)))
                log(f"PERSIST-FAILED {name}: {e}")
        level = _level(p["u7d"])
        pkey = state.setdefault(p["pool"], {})
        prev = pkey.get("alerted")           # None | "warn" | "urgent"
        reset = p.get("reset7d")
        # New weekly window (reset advanced) clears the alert memory.
        if reset and pkey.get("reset") and reset != pkey.get("reset"):
            prev = None
        pkey["reset"] = reset
        pkey["u7d"] = p["u7d"]
        if level == "ok":
            pkey["alerted"] = None
        elif level == "warn" and prev is None:
            pct = round(p["u7d"] * 100)
            _page(f"Max weekly usage WARN — {p['pool']} at {pct}%",
                  _alert_body(p, "warn"), "P2", dry)
            # DUAL-EMIT (#14988): a SEPARATE operator-addressed ('musa') row so the
            # operator's OWN early-warning path can fire directly, not on a latency
            # hop through Nazim. NOTE: the last delivery hop (this row -> operator
            # Telegram) is the hub's pen iv (tg_send), NOT ours — it is wired by the
            # pen holder (see handoff to orch-console). Until then this row is the
            # durable/attributable record + the relay's hook, not yet a phone ping.
            # A failure here is LOUD but never blocks the page to Nazim above
            # (dead-man's-switch: one channel down ≠ both silent).
            _page(f"⚠️ Heads up — {p['pool']} Claude pool at {pct}% of its weekly limit",
                  _operator_body(p, "warn"), "P2", dry, to_agent=OPERATOR_AGENT)
            pkey["alerted"] = "warn"
        elif level == "urgent" and prev != "urgent":
            pct = round(p["u7d"] * 100)
            _page(f"Max weekly usage URGENT — {p['pool']} at {pct}%",
                  _alert_body(p, "urgent"), "P1", dry)
            _page(f"⚠️ URGENT — {p['pool']} Claude pool at {pct}% of its weekly limit",
                  _operator_body(p, "urgent"), "P1", dry, to_agent=OPERATOR_AGENT)
            pkey["alerted"] = "urgent"
        log(f"{p['pool']}: 7d={round(p['u7d']*100)}% 5h={round((p.get('u5h') or 0)*100)}% "
            f"status={p.get('status')} level={level} reset={_fmt_reset(reset)}")

    # DEAD-MAN'S-SWITCH: a probe failure is LOUD, never silent.
    if failures:
        detail = "; ".join(f"{n}: {e}" for n, e in failures)
        _page("🐛 Weekly-limit monitor PROBE FAILED — usage is NOT being watched",
              f"[Max weekly-limit monitor] Could not read usage for: {detail}. "
              f"The fleet could hit a weekly limit unwarned until this is fixed.",
              "P1", dry)

    # Console-persist failure is loud too (op#9770): usage is still WATCHED (probe
    # ok), but the console's weekly-% would freeze — a stale-info regression.
    if persist_failures:
        detail = "; ".join(f"{n}: {e}" for n, e in persist_failures)
        _page("🐛 Weekly-limit monitor PERSIST FAILED — console weekly-% is STALE",
              f"[Max weekly-limit monitor] Usage is still being watched, but could "
              f"not write pool_usage for: {detail}. The console header will show a "
              f"frozen weekly-% until this is fixed.",
              "P2", dry)

    if not dry:
        try:
            HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            import time
            HEARTBEAT_FILE.write_text(str(time.time()))
        except Exception as e:
            log(f"heartbeat-write-failed: {e}")
        save_state(state)

    if as_json:
        print(json.dumps({"results": results,
                          "failures": [{"pool": n, "error": e} for n, e in failures],
                          "warn": WARN, "urgent": URGENT}, indent=2))
    # Non-zero only if EVERY pool failed (total blindness).
    return 1 if (failures and not results) else 0


def main() -> int:
    dry = "--dry-run" in sys.argv
    as_json = "--json" in sys.argv
    return run(dry=dry, as_json=as_json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as _e:
        import traceback
        traceback.print_exc()
        # Dependency-free last-resort page: the guard's own failure must surface.
        try:
            import subprocess
            subprocess.run([str(_ORCH_DIR / "scripts" / "nazim_send.sh"),
                            f"🐛 weekly_limit_monitor CRASHED — usage not being watched: {_e}"],
                           timeout=30, cwd=str(_ORCH_DIR))
        except Exception:
            pass
        sys.exit(1)
