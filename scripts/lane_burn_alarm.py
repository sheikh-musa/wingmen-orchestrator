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

Dedup: a lane that stays over threshold across consecutive runs is paged at
most once per REPAGE_MIN, tracked in a JSON state file (same shape as
priority_sla_watchdog.py's STATE_FILE convention) -- never a page per tick.
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
REPAGE_MIN = int(os.environ.get("LANE_BURN_ALARM_REPAGE_MIN", "60"))
# If this fraction (or more) of expected lanes come back unmeasurable, page once
# (dedup'd separately) -- that is a tooling-health problem, not a burn problem.
MEASURE_FAILURE_PAGE_FRACTION = float(os.environ.get("LANE_BURN_ALARM_FAILURE_FRACTION", "0.5"))
MEASURE_FAILURE_REPAGE_MIN = int(os.environ.get("LANE_BURN_ALARM_FAILURE_REPAGE_MIN", "180"))


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
    {'total': int, 'nmsg': int} or None if nothing parseable at all (empty
    transcript -- distinct from "below threshold", still a real 0 reading)."""
    total = 0
    nmsg = 0
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
        total += (
            (usage.get("input_tokens") or 0)
            + (usage.get("output_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
        )
        nmsg += 1
    if not saw_any_line and not lines:
        return {"total": 0, "nmsg": 0}
    return {"total": total, "nmsg": nmsg}


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
        return {"status": "ok", "tph": tph, "total": summed["total"], "nmsg": summed["nmsg"], "agent_id": agent_id}
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

    for b in breaches:
        agent_id = b["agent_id"]
        key = f"breach:{agent_id}"
        if not _should_page(state, key, now, REPAGE_MIN):
            log(f"breach {agent_id} tph={b['tph']:.0f} suppressed (repage backoff)")
            continue
        subject = f"⚠️ lane burn: {agent_id} at {b['tph']:,.0f} tok/hr (threshold {args.threshold_tph:,.0f})"
        body = (
            f"TL;DR: {agent_id} is burning tokens well above the normal-work ceiling -- check for a "
            f"false-wake loop or an oversized re-read context.\n\n"
            f"Measured {b['total']:,} tokens over the last {args.window_min}min window "
            f"({b['nmsg']} messages with usage) -> {b['tph']:,.0f} tokens/hour.\n"
            f"Threshold is {args.threshold_tph:,.0f}/hr (set from this session's own readings: idle=0, "
            f"normal work=0.5-3.65M/60min, the PR#649 false-wake episode=81-93M/60min)."
        )
        if send_bus_alert(subject, body, "P1", args.dry_run):
            state[key] = now.isoformat()

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
