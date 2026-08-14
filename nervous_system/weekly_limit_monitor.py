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

We track TWO independent pools: Musa (~/.wingmen/keys/musa-oauth-token) and Syed
(~/.wingmen/keys/syed-oauth-token). BOTH are pinned to their STABLE per-account key
files — NOT the mutable orchestrator-.env CLAUDE_CODE_OAUTH_TOKEN default. This is
the op#12030 fix: the "Musa" pool used to read .env's CLAUDE_CODE_OAUTH_TOKEN, but
that default is flipped between accounts during token-moves (it was flipped Musa->Syed
in the op#11899 crisis). After that flip the monitor silently probed the SYED token
under BOTH labels -> "Musa" and "Syed" reported IDENTICAL utilization/reset (falsely
read as a "unified pool"), masking that Musa was CLEAR while Syed maxed to 100%
(~2h of lost headroom). Pinning each pool to its own 0600 key file makes a false
'full' from token-conflation structurally impossible.

DEAD-MAN'S-SWITCH (doctrine: a silent-fail monitor is worse than none): every run
writes a heartbeat; a probe failure PAGES loudly rather than passing silently; an
unhandled crash pages via a dependency-free path. This is ALERT-ONLY — it never
takes a fleet action, so it is ungated (a safety signal is never silenced).
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ORCH_DIR))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_ORCH_DIR / ".env")
from nervous_system.pool_pace import compute_pool_pace  # noqa: E402  (pure PACE math)

API_URL = "https://api.anthropic.com/v1/messages"
PROBE_BODY = json.dumps(
    {"model": "claude-haiku-4-5", "max_tokens": 1,
     "messages": [{"role": "user", "content": "hi"}]}).encode()

WARN = float(os.environ.get("WEEKLY_LIMIT_WARN", "0.75"))    # ~75%
URGENT = float(os.environ.get("WEEKLY_LIMIT_URGENT", "0.90"))  # ~90%

STATE_FILE = _ORCH_DIR / "logs" / "weekly_limit_monitor_state.json"
HEARTBEAT_FILE = _ORCH_DIR / "logs" / "weekly_limit_monitor_heartbeat"
LOG_FILE = _ORCH_DIR / "logs" / "weekly_limit_monitor.log"

# op#13183 (operator directive, armed by cc-fleet-health via agent_messages #21746):
# when Musa 7d crosses URGENT and this conditional is ARMED, the singleton
# COORDINATORS (SRE/console/hub/cai) move Musa->Syed (checkpoint-first, sticky
# pointer). This monitor is the AUTONOMOUS TRIGGER — it pages the SRE (+console) to
# EXECUTE the supervised move; the durable armed-state file is the SSOT (survives an
# SRE recycle). The emit is DEDUPED once per weekly window and gated on status=armed,
# so it stops firing once the move is executed (status=executed).
OP13183_STATE = _ORCH_DIR / "state" / "op13183_singleton_move.json"


def _op13183_armed() -> bool:
    """True if the op#13183 singleton pool-move conditional is ARMED. Missing file =>
    NOT armed (never page for an unarmed conditional). Present-but-unreadable =>
    fail-OPEN (page): a spurious page is cheaper than a missed cliff, and the caller
    logs the ambiguity."""
    try:
        return json.loads(OP13183_STATE.read_text()).get("status") == "armed"
    except FileNotFoundError:
        return False
    except Exception:
        return True  # present but unreadable: fail-open, better than a silent miss


def _op13183_body(p: dict) -> str:
    pct = round(p["u7d"] * 100)
    return (
        f"[op#13183 TRIGGER] Musa 7d weekly usage = {pct}% (>= URGENT "
        f"{round(URGENT*100)}%) -> the singleton-coordinator pool-move conditional has "
        f"FIRED.\n\n"
        f"WHAT: per operator op#13183, at Musa 90% the singleton coordinators "
        f"(cc-fleet-health/SRE, orch-console, cc-orchestrator/hub, cai) move "
        f"Musa->Syed (Syed has ~22d runway). CHECKPOINT-FIRST per body, then "
        f"switch_lane_token->Syed + flip the default pointer so it sticks.\n"
        f"WHAT TO DO (SRE executes): follow reports/op13183-singleton-move-runbook.md. "
        f"Reachability is NOT uniform — the HUB is on the VPS (not switchable from the "
        f"Mini) and the SRE cannot self-switch; those two need an EXTERNAL fire "
        f"(console), like reset_nazim was routed to the SRE. Report as each fires; set "
        f"the armed-state to status=executed when done so this trigger stops.\n"
        f"(source: weekly_limit_monitor autonomous trigger; dedup: once per weekly "
        f"window)")


def _op13183_page(p: dict, dry: bool) -> None:
    """Page the SRE (+console) that the op#13183 conditions are met. Both rows so the
    SRE is woken to execute AND console can fire the hub (VPS) + the SRE's own move."""
    subj = (f"op#13183 TRIGGER — Musa >= {round(URGENT*100)}%: EXECUTE singleton-"
            f"coordinator pool-move (checkpoint-first -> Syed)")
    body = _op13183_body(p)
    _page(subj, body, "P1", dry, to_agent="cc-fleet-health")
    _page(subj, body, "P1", dry, to_agent="orch-console")
    log(f"op#13183 TRIGGER fired (Musa {round(p['u7d']*100)}%)")

# Pool name -> how to load its OAuth token. Value is (kind, ref).
# BOTH pinned to stable per-account key files (op#12030): a pool must ALWAYS be
# probed with its OWN token, never the mutable .env CLAUDE_CODE_OAUTH_TOKEN default
# (which token-moves flip between accounts -> silent cross-account conflation).
POOLS = {
    "Musa": ("file", str(Path.home() / ".wingmen" / "keys" / "musa-oauth-token")),
    "Syed": ("file", str(Path.home() / ".wingmen" / "keys" / "syed-oauth-token")),
    # musa2 (op#12617): a SEPARATE weekly pool from Musa-1 — verified by an
    # INDEPENDENT unified-7d utilization AND a DIFFERENT reset epoch on a live probe
    # (Musa-1 44%/reset 08-19 08:00 vs musa2 8%/reset 08-18 21:00, 2026-08-13). It is
    # a genuine offload valve: work run on musa2 does NOT draw down Musa-1's weekly
    # pool. Pinned to its own stable key file (fp e1dfa48eec85), same op#12030 rule.
    "musa2": ("file", str(Path.home() / ".wingmen" / "keys" / "musa2-oauth-token")),
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


def _persist_reading(p: dict, pace: "PaceResult | None" = None) -> None:
    """UPSERT the latest reading for one pool into public.pool_usage — the console's
    read path (op#9770). One row per pool, overwritten in place; `updated_at`
    stamps THIS reading's freshness so the console can grey out a stale one.
    Raises on failure so the caller can surface it LOUD (a silent-fail persist
    would let the console show a frozen number — the exact stale-info bug op#9770
    is closing). Uses the SERVICE_ROLE via DATABASE_URL, same as the bus alert.

    op#12617: also writes the ADDITIVE pace columns (pace / projected_pct /
    runway_days) when a PaceResult is supplied. runway=inf (pool not burning) is
    stored as NULL — the console renders 'inf' as 'no runway concern'."""
    import psycopg
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("no DATABASE_URL for pool_usage persist")
    pct7 = round(p["u7d"] * 100, 1)
    pct5 = round((p.get("u5h") or 0) * 100, 1)
    reset = p.get("reset7d")
    reset_at = datetime.fromtimestamp(reset, tz=timezone.utc) if reset else None
    pace_val = proj_val = runway_val = None
    if pace is not None:
        pace_val = round(pace.pace, 3) if pace.pace is not None else None
        proj_val = round(pace.projected_pct, 1) if pace.projected_pct is not None else None
        runway_val = (round(pace.runway_days, 2)
                      if math.isfinite(pace.runway_days) else None)
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO pool_usage (pool, pct_7d, pct_5h, resets_at, status_7d, "
            "  pace, projected_pct, runway_days, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (pool) DO UPDATE SET "
            "  pct_7d=EXCLUDED.pct_7d, pct_5h=EXCLUDED.pct_5h, "
            "  resets_at=EXCLUDED.resets_at, status_7d=EXCLUDED.status_7d, "
            "  pace=EXCLUDED.pace, projected_pct=EXCLUDED.projected_pct, "
            "  runway_days=EXCLUDED.runway_days, updated_at=now()",
            (p["pool"], pct7, pct5, reset_at, p.get("status7d"),
             pace_val, proj_val, runway_val))
        c.commit()


def _persist_history(p: dict) -> None:
    """APPEND this reading to pool_usage_history (op#12617) — the trail that lets a
    later poll compute a trailing-24h burn (runway needs two SAME-WINDOW readings;
    the one-row-per-pool pool_usage table cannot hold history). `resets_at` is
    stamped so burn is only ever taken within a window. Raises on failure (LOUD)."""
    import psycopg
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("no DATABASE_URL for pool_usage_history append")
    pct7 = round(p["u7d"] * 100, 1)
    pct5 = round((p.get("u5h") or 0) * 100, 1)
    reset = p.get("reset7d")
    reset_at = datetime.fromtimestamp(reset, tz=timezone.utc) if reset else None
    with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO pool_usage_history (pool, pct_7d, pct_5h, resets_at) "
            "VALUES (%s,%s,%s,%s)",
            (p["pool"], pct7, pct5, reset_at))
        c.commit()


# Only compute a runway once we have >= this much SAME-WINDOW trailing history.
# 20h (not a full 24h) tolerates poll jitter while still giving a stable ~daily
# burn — a shorter span would extrapolate noisily and false-page.
_MIN_TRAIL_HOURS = 20


def _load_prior_same_window(pool: str, reset_at, now) -> "tuple | None":
    """Newest SAME-WINDOW history reading at least _MIN_TRAIL_HOURS old, for the
    trailing-24h burn. Returns (pct_prev, recorded_at) or None (not enough history
    yet -> no runway this poll). Same-window (resets_at match) so a window reset
    never manufactures a negative burn. Read-only; returns None on any DB error so
    a history-read blip degrades runway gracefully without blocking the safety
    threshold path (the caller's absolute-threshold page is unaffected)."""
    import psycopg
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn or reset_at is None:
        return None
    cutoff = now - timedelta(hours=_MIN_TRAIL_HOURS)
    try:
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            cur.execute(
                "SELECT pct_7d, recorded_at FROM pool_usage_history "
                "WHERE pool=%s AND resets_at=%s AND recorded_at <= %s "
                "ORDER BY recorded_at DESC LIMIT 1",
                (pool, reset_at, cutoff))
            row = cur.fetchone()
    except Exception as e:
        log(f"prior-history-read-failed {pool}: {e}")
        return None
    if not row:
        return None
    return (float(row[0]), row[1])


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


def _pace_operator_body(p: dict, pace) -> str:
    """ELI5 pace warning for the operator's phone (routed like the threshold
    warnings: to 'musa', subject '⚠️…', picked up by weekly_alert_relay). This is
    a PACE signal — the pool is not full yet, but at the current rate it is on
    track to run out BEFORE the weekly window resets."""
    pct = round(p["u7d"] * 100)
    proj = f"{pace.projected_pct:.0f}%" if pace.projected_pct is not None else "?"
    if math.isfinite(pace.runway_days):
        runway = f"{pace.runway_days:.1f} days (resets in {pace.days_to_reset:.1f})"
    else:
        runway = "n/a yet"
    # Prefer another pool with headroom as the offload; musa2 is a real valve.
    return (
        f"⚠️ Pace warning — the {p['pool']} Claude pool is on track to run out "
        f"before its weekly reset.\n\n"
        f"WHAT: {p['pool']} is at {pct}% now, but at the current burn it PROJECTS to "
        f"{proj} by week-end (runway {runway}). {'; '.join(pace.reasons)}.\n"
        f"WHY: this is the EARLY pace signal (op#12617) — it fires before the 75/90% "
        f"absolute alarm so you can shift work now rather than stall at 100%.\n"
        f"WHAT TO DO: move heavy lanes to a pool with headroom (Syed / musa2 are "
        f"separate weekly pools), and/or flip lanes to Sonnet (separate limit). If "
        f"you're fine pacing until reset, nothing needed.")


def _pace_body(p: dict, pace) -> str:
    """The fleet-facing (orch-console) pace record — the metrics in full."""
    pct = round(p["u7d"] * 100)
    proj = f"{pace.projected_pct:.0f}%" if pace.projected_pct is not None else "?"
    pace_r = f"{pace.pace:.2f}" if pace.pace is not None else "?"
    runway = (f"{pace.runway_days:.1f}d" if math.isfinite(pace.runway_days)
              else "inf (not burning)")
    return (
        f"[Max weekly-PACE monitor] {p['pool']} is on track to exhaust before reset.\n\n"
        f"WHAT: used={pct}% at {pace.elapsed_frac*100:.0f}% into the week -> "
        f"pace={pace_r} (1.0=on budget), projected end-of-week={proj}, "
        f"runway={runway} vs {pace.days_to_reset:.1f}d to reset.\n"
        f"REASONS: {'; '.join(pace.reasons)}.\n"
        f"WHAT TO DO: offload heavy lanes to a pool with headroom (Syed / musa2 are "
        f"SEPARATE weekly pools — a genuine valve), flip lanes to Sonnet, or pace "
        f"until the {_fmt_reset(p.get('reset7d'))} reset.\n"
        f"(source: unified-7d-utilization + trailing-24h burn from pool_usage_history)")


def run(dry: bool = False, as_json: bool = False) -> int:
    state = load_state()
    now = datetime.now(timezone.utc)
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
        # PACE layer (op#12617): compute pace / projected / runway from THIS reading
        # + the trailing-24h same-window prior. Pure math (pool_pace); read-only
        # history lookup degrades to None (no runway) on any blip, so it can never
        # block the absolute-threshold safety path below.
        reset_epoch = p.get("reset7d")
        resets_at = (datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
                     if reset_epoch else None)
        pace = None
        if resets_at is not None:
            prior = _load_prior_same_window(p["pool"], resets_at, now)
            pace = compute_pool_pace(now, p["u7d"] * 100.0, resets_at, prior=prior)
            # JSON-safe snapshot for --json (the dataclass itself is not serializable;
            # keep the live `pace` local var for persist + paging).
            p["pace"] = {
                "elapsed_frac": round(pace.elapsed_frac, 4),
                "pace": (round(pace.pace, 3) if pace.pace is not None else None),
                "projected_pct": (round(pace.projected_pct, 1)
                                  if pace.projected_pct is not None else None),
                "burn_per_day": (round(pace.burn_per_day, 3)
                                 if pace.burn_per_day is not None else None),
                "runway_days": (round(pace.runway_days, 2)
                                if math.isfinite(pace.runway_days) else None),
                "days_to_reset": round(pace.days_to_reset, 2),
                "should_page": pace.should_page,
                "reasons": pace.reasons,
            }
        # Persist the reading for the console (op#9770 + op#12617 pace columns). A
        # persist failure does NOT block alerting (the primary safety function) —
        # surfaced LOUD below so the console never silently shows a frozen number.
        if not dry:
            try:
                _persist_reading(p, pace)
                _persist_history(p)   # append the trail for the next poll's runway
            except Exception as e:
                persist_failures.append((name, str(e)))
                log(f"PERSIST-FAILED {name}: {e}")
        level = _level(p["u7d"])
        pkey = state.setdefault(p["pool"], {})
        prev = pkey.get("alerted")           # None | "warn" | "urgent"
        reset = p.get("reset7d")
        # New weekly window (reset advanced) clears the alert memory (both the
        # absolute-threshold memory and the pace memory — a fresh window pages afresh).
        if reset and pkey.get("reset") and reset != pkey.get("reset"):
            prev = None
            pkey["pace_alerted"] = None
            pkey["op13183_fired"] = None   # re-arm the op#13183 trigger for the new window
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

        # op#13183 AUTONOMOUS TRIGGER: at Musa >= URGENT, if the singleton pool-move
        # conditional is ARMED, page the SRE (+console) to EXECUTE the supervised move.
        # A SEPARATE `if` (not the alert transition above) so it fires whenever Musa is
        # urgent-and-not-yet-fired-this-window, independent of the alert-memory state.
        # OWN try/except: a fault here must NEVER break the weekly safety pages above
        # (dead-man's-switch). Deduped once per window (op13183_fired, cleared on a new
        # window with the other alert memory).
        if (p["pool"] == "Musa" and level == "urgent"
                and _op13183_armed() and not pkey.get("op13183_fired")):
            try:
                _op13183_page(p, dry)
                pkey["op13183_fired"] = True
            except Exception as e:  # noqa: BLE001 — never let this break the core pages
                log(f"op#13183-TRIGGER-EMIT-FAILED (non-fatal): {e}")
                print(f"[weekly-limit] op#13183 trigger emit failed: {e}",
                      file=sys.stderr)

        # PACE page (op#12617): the pool is on track to EXHAUST before its weekly
        # reset (projected>100% past the early-window floor, OR runway<days-to-reset)
        # — a distinct, earlier signal than the absolute 75/90% alarm. Deduped once
        # per window (pace_alerted), cleared on a new window above. P2: advisory.
        if pace is not None and pace.should_page and not pkey.get("pace_alerted"):
            _page(f"Max weekly PACE — {p['pool']} on track to exhaust before reset",
                  _pace_body(p, pace), "P2", dry)
            # Operator-addressed twin (routed by weekly_alert_relay via subject '⚠️').
            _page(f"⚠️ Pace warning — {p['pool']} Claude pool may run out before reset",
                  _pace_operator_body(p, pace), "P2", dry, to_agent=OPERATOR_AGENT)
            pkey["pace_alerted"] = True
            log(f"{p['pool']}: PACE-PAGE {'; '.join(pace.reasons)}")

        if pace is not None:
            _pr = (f"pace={pace.pace:.2f}" if pace.pace is not None else "pace=?")
            _rw = (f"{pace.runway_days:.1f}d" if math.isfinite(pace.runway_days)
                   else "inf")
            _pj = (f"{pace.projected_pct:.0f}%" if pace.projected_pct is not None
                   else "?")
            log(f"{p['pool']}: elapsed={pace.elapsed_frac*100:.0f}% {_pr} "
                f"projected={_pj} runway={_rw} dtr={pace.days_to_reset:.1f}d")
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
