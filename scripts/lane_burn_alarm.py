#!/usr/bin/env python3
"""lane_burn_alarm.py — per-lane tokens/hour burn alarm (op#35929/35930).

Musa directive (bus #83575, orch-console agreed #83579): "a per-lane burn
alarm (tokens per hour ...) that pages me." This is the code-enforced half of
that ask — detection + paging, not a promise to watch dashboards.

METHOD: for every live lane (agent_status.status != 'offline'), find its tmux
pane's cwd (locally via `tmux list-panes`, or over `ssh gzb` for lanes on the
gzb host), map that cwd to its Claude Code transcript directory
(~/.claude/projects/<cwd-with-'/'-and-'.'-escaped-to-'-'>/*.jsonl, picking the
most-recently-modified file), and sum each message's
input+output+cache_creation+cache_read token usage for messages timestamped
within the trailing window (default 60min) to get an exact tokens/hour rate.
This is the same live-transcript-delta method cc-fleet-health used by hand
this session to measure Musa/Syed pool pace — now it runs on a schedule and
pages instead of requiring a human (or an agent) to go look.

THRESHOLD (default 40,000,000 tokens/hour, LANE_BURN_ALARM_THRESHOLD_TPH):
raised from an initial 15M (orch-console #83643, after cc-fleet-health's
op#35929 zero-waste 24h report #656 showed several genuinely busy lanes
sustaining 18-33M tok/hr on AVERAGE across a full day -- 15M would have
paged continuously on real work, not waste). 40M/hr sits above every
legitimate 24h-average rate measured in that report (highest was
cc-cosem-exams-1 at ~32.7M/hr; cc-fleet-health itself averaged ~48.7M/hr but
that figure is itself under investigation as a waste source, not a baseline
to clear) while still catching a repeat of the PR#649 false-wake episode
(cc-fleet-health/orch-console at 81-93M tokens/60min).

FAIL-VISIBLE, NEVER A GUESS (Musa's explicit instruction): a lane whose tmux
pane can't be found, whose host is unreachable over ssh, or whose transcript
directory/file can't be read is reported as "could not measure" and
EXCLUDED from the breach check -- never assigned a fabricated or
extrapolated number. If more than MEASURE_FAILURE_PAGE_FRACTION of expected
lanes are unmeasurable in one run, that is itself paged once (dedup'd) as a
tooling-health signal, separate from any burn breach.

Dedup (orch-console #85152/#85266, second round of repeat pages on the same
acknowledged-busy lanes): a lane that stays over threshold is suppressed for
BREACH_SUPPRESS_MIN (default 4h) -- UNLESS its rate has
risen BREACH_RATE_RISE_FRAC (default 50%) or more since the last page, or its
context fill (from context_health_watchdog's own gauge) has crossed
BREACH_CONTEXT_FILL_ESCAPE_PCT (default 90%) -- either escape re-pages
immediately, because those are genuinely new information, not a repeat of
what was already acknowledged. Tracked in the same JSON state file
(priority_sla_watchdog.py's STATE_FILE convention), now storing {ts, tph} per
breach key instead of a bare timestamp so the rate-rise check has something
to compare against.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = Path(os.path.expanduser("~/wingmen/fleet-health/state/lane_burn_alarm_state.json"))
LOG_FILE = ORCH_DIR / "logs" / "lane_burn_alarm.log"

GZB_SSH_ALIAS = os.environ.get("LANE_BURN_ALARM_GZB_SSH", "gzb")
REMOTE_HOSTS = {"gzbai", "gzb"}  # agent_status.host spellings seen for the gzb box

WINDOW_MIN_DEFAULT = int(os.environ.get("LANE_BURN_ALARM_WINDOW_MIN", "60"))
THRESHOLD_TPH_DEFAULT = int(os.environ.get("LANE_BURN_ALARM_THRESHOLD_TPH", "40000000"))
# If this fraction (or more) of expected lanes come back unmeasurable, page once
# (dedup'd separately) -- that is a tooling-health problem, not a burn problem.
MEASURE_FAILURE_PAGE_FRACTION = float(os.environ.get("LANE_BURN_ALARM_FAILURE_FRACTION", "0.5"))
MEASURE_FAILURE_REPAGE_MIN = int(os.environ.get("LANE_BURN_ALARM_FAILURE_REPAGE_MIN", "180"))

# Per-lane breach repage dedup (orch-console #85152/#85266, second round of repeat pages on
# the same already-acknowledged-busy lanes): suppress for this long UNLESS an escape fires.
BREACH_SUPPRESS_MIN = int(os.environ.get("LANE_BURN_ALARM_BREACH_SUPPRESS_MIN", "240"))
BREACH_RATE_RISE_FRAC = float(os.environ.get("LANE_BURN_ALARM_BREACH_RATE_RISE_FRAC", "0.5"))
BREACH_CONTEXT_FILL_ESCAPE_PCT = int(os.environ.get("LANE_BURN_ALARM_BREACH_CONTEXT_FILL_ESCAPE_PCT", "90"))


def log(line: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without any subprocess/DB)
# ---------------------------------------------------------------------------

def escape_cwd(cwd: str) -> str:
    """Claude Code's own project-dir escaping: both '/' and '.' -> '-'."""
    return cwd.replace("/", "-").replace(".", "-")


def pick_latest_jsonl(filenames: list[str], mtimes: dict[str, float]) -> str | None:
    """Given a list of *.jsonl filenames in a project dir and their mtimes,
    return the most recently modified one, or None if the list is empty."""
    if not filenames:
        return None
    return max(filenames, key=lambda f: mtimes.get(f, 0.0))


def sum_usage_lines(lines: list[str], cutoff: datetime) -> dict | None:
    """Sum usage tokens for JSONL lines timestamped >= cutoff. Returns
    {'total': int, 'nmsg': int, 'cache_read': int} or None if nothing parseable
    at all (empty transcript -- distinct from "below threshold", still a real 0
    reading). cache_read is tracked separately (it is already inside total) so
    callers can report what SHARE of the burn is cheap cache reads vs real
    input/output/cache-write work (console #85266: add it to the alert text)."""
    total = 0
    nmsg = 0
    cache_read = 0
    saw_any_line = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        saw_any_line = True
        ts = obj.get("timestamp")
        if not ts:
            continue
        try:
            tdt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if tdt < cutoff:
            continue
        msg = obj.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not usage:
            continue
        cache_r = usage.get("cache_read_input_tokens") or 0
        total += (
            (usage.get("input_tokens") or 0)
            + (usage.get("output_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + cache_r
        )
        cache_read += cache_r
        nmsg += 1
    if not saw_any_line and not lines:
        return {"total": 0, "nmsg": 0, "cache_read": 0}
    return {"total": total, "nmsg": nmsg, "cache_read": cache_read}


def tokens_per_hour(total_tokens: int, window_min: int) -> float:
    if window_min <= 0:
        return 0.0
    return total_tokens * (60.0 / window_min)


def evaluate_lane(measurement: dict | None, threshold_tph: float) -> str:
    """'breach' | 'ok' | 'unmeasured' from a measurement dict (or None)."""
    if measurement is None:
        return "unmeasured"
    if measurement["tph"] >= threshold_tph:
        return "breach"
    return "ok"


# ---------------------------------------------------------------------------
# Live discovery (subprocess/DB-backed; thin, not unit tested directly --
# tested instead via the pure helpers above plus the orchestration test that
# injects fakes for _list_panes/_read_transcript).
# ---------------------------------------------------------------------------

def _dsn() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _live_lanes(cur) -> list[dict]:
    cur.execute(
        """
        SELECT agent_id, host, tmux_session
        FROM agent_status
        WHERE COALESCE(status, '') <> 'offline'
          AND tmux_session IS NOT NULL
        ORDER BY agent_id
        """
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _list_panes_local() -> dict[str, str]:
    """session -> cwd, for every local tmux pane."""
    out = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_path}"],
        capture_output=True, text=True, timeout=15,
    )
    panes: dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            panes[parts[0]] = parts[1]
    return panes


def _list_panes_remote(ssh_alias: str) -> dict[str, str]:
    """session -> cwd, for every tmux pane on the gzb host, over ssh."""
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", ssh_alias,
         "tmux list-panes -a -F '#{session_name}\t#{pane_current_path}'"],
        capture_output=True, text=True, timeout=20,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ssh {ssh_alias} tmux list-panes failed: {out.stderr.strip()[:200]}")
    panes: dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            panes[parts[0]] = parts[1]
    return panes


def _read_transcript_lines_local(cwd: str) -> list[str]:
    proj_dir = Path(os.path.expanduser("~/.claude/projects")) / escape_cwd(cwd)
    if not proj_dir.is_dir():
        raise FileNotFoundError(f"no project dir {proj_dir}")
    files = list(proj_dir.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"no transcript in {proj_dir}")
    latest = max(files, key=lambda f: f.stat().st_mtime)
    return latest.read_text(encoding="utf-8", errors="replace").splitlines()


def _read_transcript_lines_remote(ssh_alias: str, cwd: str) -> list[str]:
    proj_dir = f"~/.claude/projects/{escape_cwd(cwd)}"
    # Find the newest *.jsonl, then tail it (bounded transfer -- we only need
    # messages inside the trailing window, which are always near the end of
    # an append-only transcript).
    find_cmd = (
        f"latest=$(ls -t {proj_dir}/*.jsonl 2>/dev/null | head -1); "
        f"[ -n \"$latest\" ] && tail -n 2000 \"$latest\""
    )
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", ssh_alias, find_cmd],
        capture_output=True, text=True, timeout=25,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise FileNotFoundError(f"ssh {ssh_alias} could not read transcript for {cwd}: {out.stderr.strip()[:200]}")
    return out.stdout.splitlines()


def measure_lane(lane: dict, window_min: int, now: datetime) -> dict:
    """Returns {'status': 'ok'|'error', 'tph': float, 'total': int, 'nmsg': int,
    'reason': str} -- 'error' entries carry a human reason, never a number."""
    agent_id = lane["agent_id"]
    host = (lane.get("host") or "").strip()
    session = lane["tmux_session"]
    cutoff = now - timedelta(minutes=window_min)
    try:
        if host in REMOTE_HOSTS:
            panes = _list_panes_remote(GZB_SSH_ALIAS)
        else:
            panes = _list_panes_local()
        cwd = panes.get(session)
        if not cwd:
            return {"status": "error", "reason": f"no live tmux pane for session '{session}'"}
        if host in REMOTE_HOSTS:
            lines = _read_transcript_lines_remote(GZB_SSH_ALIAS, cwd)
        else:
            lines = _read_transcript_lines_local(cwd)
        summed = sum_usage_lines(lines, cutoff)
        tph = tokens_per_hour(summed["total"], window_min)
        cache_read_share = (summed["cache_read"] / summed["total"]) if summed["total"] else 0.0
        return {
            "status": "ok", "tph": tph, "total": summed["total"], "nmsg": summed["nmsg"],
            "cache_read": summed["cache_read"], "cache_read_share": cache_read_share,
            "agent_id": agent_id,
        }
    except Exception as exc:  # noqa: BLE001 -- fail visible per-lane, never crash the run
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}", "agent_id": agent_id}


# ---------------------------------------------------------------------------
# State (dedup) + bus alert
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _should_page(state: dict, key: str, now: datetime, repage_min: int) -> bool:
    last = state.get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now - last_dt) >= timedelta(minutes=repage_min)


def should_repage_breach(
    prior,
    now: datetime,
    current_tph: float,
    current_pct: "int | None",
    *,
    suppress_min: int,
    rate_rise_frac: float,
    pct_escape: int,
) -> bool:
    """True if a lane already paged for breach should page again now
    (console #85152/#85266: stop re-paging an already-acknowledged lane every
    tick, but never suppress genuinely new information).

    `prior` is this lane's breach-state entry from the last page: {'ts':
    iso8601, 'tph': float}, or anything else (missing key, a bare string from
    before this field existed, a corrupt value) -- treated as "no prior data",
    which pages. Fail toward alerting, never silently suppress forever, same
    posture as the rest of this file's dedup.

    Two escapes always re-page regardless of suppress_min, because they are
    new information, not a repeat of what was already seen+acknowledged:
      - current_pct (context fill %, from context_health_watchdog's gauge)
        has crossed pct_escape. None (gauge unavailable) never escapes --
        absence of data is not evidence of a crossed threshold.
      - current_tph has risen by rate_rise_frac or more since the tph that
        was actually paged last time.
    Otherwise: page again only once suppress_min has elapsed since the last
    page, same shape as the plain time-based dedup elsewhere in this file."""
    if not isinstance(prior, dict) or "ts" not in prior:
        return True
    try:
        last_dt = datetime.fromisoformat(prior["ts"])
    except (ValueError, TypeError):
        return True
    if current_pct is not None and current_pct >= pct_escape:
        return True
    last_tph = prior.get("tph")
    if isinstance(last_tph, (int, float)) and last_tph > 0 and current_tph >= last_tph * (1 + rate_rise_frac):
        return True
    return (now - last_dt) >= timedelta(minutes=suppress_min)


def breach_alert_body(b: dict, window_min: int, threshold_tph: float, context_pct: "int | None") -> str:
    """Pure alert-text builder (console #85266: add context fill % and
    cache-read share so the page itself carries enough to triage without a
    follow-up query). context_pct is None when the gauge was unavailable or
    had no reading for this agent -- reported as 'unknown', never guessed."""
    fill_line = f"{context_pct}%" if context_pct is not None else "unknown (gauge had no reading)"
    cache_read_share = b.get("cache_read_share")
    share_line = f"{cache_read_share * 100:.0f}%" if cache_read_share is not None else "unknown"
    return (
        f"TL;DR: {b['agent_id']} is burning tokens well above the normal-work ceiling -- check for a "
        f"false-wake loop or an oversized re-read context.\n\n"
        f"Measured {b['total']:,} tokens over the last {window_min}min window "
        f"({b['nmsg']} messages with usage) -> {b['tph']:,.0f} tokens/hour.\n"
        f"Context fill: {fill_line}. Cache-read share of this window's tokens: {share_line} "
        f"(high = mostly re-reading existing context, not new work).\n"
        f"Threshold is {threshold_tph:,.0f}/hr (set from this session's own readings: idle=0, "
        f"normal work=0.5-3.65M/60min, the PR#649 false-wake episode=81-93M/60min)."
    )


def _context_fill_map() -> dict:
    """{agent_id: pct} from context_health_watchdog's own gauge -- reused rather than
    re-querying cc_session_costs independently, so this file's 'context fill' figure can
    never drift from the one the context watchdog itself classifies on. Failure here
    (import, DB, gauge unreachable) must never crash the burn alarm -- it is an enrichment,
    not this file's primary job -- so any error just means every agent reports 'unknown'."""
    try:
        from scripts import context_health_watchdog as chw
        return {a.agent: a.pct for a in chw.read_context_gauge()}
    except Exception as exc:  # noqa: BLE001 -- enrichment only, never fatal to this run
        log(f"context fill map unavailable: {type(exc).__name__}: {exc}")
        return {}


def send_bus_alert(subject: str, body: str, priority: str, dry_run: bool) -> bool:
    if dry_run:
        log(f"DRY-RUN bus {priority}: {subject}")
        return True
    env = dict(os.environ)
    env["CC_BASE_AGENT_ID"] = "cc-fleet-health"
    r = subprocess.run(
        [sys.executable, str(ORCH_DIR / "scripts" / "bus_send.py"),
         "--from", "cc-fleet-health", "--to", "orch-console",
         "--type", "update", "--priority", priority,
         "--subject", subject, "--body", body],
        cwd=str(ORCH_DIR), env=env, capture_output=True, text=True,
    )
    ok = r.returncode == 0 and "SENT" in r.stdout
    log(f"bus {'ok' if ok else 'FAILED'}: {(r.stdout + r.stderr).strip()[:200]}")
    return ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-min", type=int, default=WINDOW_MIN_DEFAULT)
    p.add_argument("--threshold-tph", type=float, default=THRESHOLD_TPH_DEFAULT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="print full per-lane results as JSON")
    args = p.parse_args(argv)

    import psycopg2  # local import: keeps the pure helpers importable test-side without a DB driver
    from dotenv import load_dotenv

    load_dotenv(ORCH_DIR / ".env")  # launchd gives this process no shell-sourced env (#85029)
    dsn = _dsn()
    if not dsn:
        print("lane_burn_alarm: no DATABASE_URL/SUPABASE_DB_URL in env", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            lanes = _live_lanes(cur)
    finally:
        conn.close()

    results = []
    for lane in lanes:
        results.append(measure_lane(lane, args.window_min, now))

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    state = _load_state()
    breaches = [r for r in results if r["status"] == "ok" and r["tph"] >= args.threshold_tph]
    errors = [r for r in results if r["status"] == "error"]
    context_pct_by_agent = _context_fill_map() if breaches else {}

    for b in breaches:
        agent_id = b["agent_id"]
        key = f"breach:{agent_id}"
        context_pct = context_pct_by_agent.get(agent_id)
        if not should_repage_breach(
            state.get(key), now, b["tph"], context_pct,
            suppress_min=BREACH_SUPPRESS_MIN, rate_rise_frac=BREACH_RATE_RISE_FRAC,
            pct_escape=BREACH_CONTEXT_FILL_ESCAPE_PCT,
        ):
            log(f"breach {agent_id} tph={b['tph']:.0f} suppressed (already acknowledged, no escape)")
            continue
        subject = f"⚠️ lane burn: {agent_id} at {b['tph']:,.0f} tok/hr (threshold {args.threshold_tph:,.0f})"
        body = breach_alert_body(b, args.window_min, args.threshold_tph, context_pct)
        if send_bus_alert(subject, body, "P1", args.dry_run):
            state[key] = {"ts": now.isoformat(), "tph": b["tph"]}

    if lanes:
        failure_fraction = len(errors) / len(lanes)
        if failure_fraction >= MEASURE_FAILURE_PAGE_FRACTION:
            key = "measure_failure"
            if _should_page(state, key, now, MEASURE_FAILURE_REPAGE_MIN):
                reasons = "; ".join(f"{e.get('agent_id', '?')}: {e['reason']}" for e in errors[:10])
                subject = f"lane_burn_alarm: {len(errors)}/{len(lanes)} lanes unmeasurable"
                body = (
                    f"TL;DR: could not measure {len(errors)} of {len(lanes)} live lanes this run -- "
                    f"tooling problem, not a reported burn number (no number was guessed for any of them).\n\n"
                    f"{reasons}"
                )
                if send_bus_alert(subject, body, "P2", args.dry_run):
                    state[key] = now.isoformat()

    _save_state(state)
    log(f"run complete: {len(lanes)} lanes, {len(breaches)} breach, {len(errors)} unmeasurable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
