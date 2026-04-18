#!/usr/bin/env python3
"""
TASK-043: Mac Mini process audit + baseline measurement.

Three phases:
  Phase 1 (default)  — dry-run discovery, no kills. Posts report to agent_messages.
  Phase 2 (--execute) — kills SAFE_KILL bucket after agent_messages approval.
  Phase 3 (--baseline) — 2-hour CPU baseline measurement with verdict.

Usage:
  python scripts/audit_mac_mini.py            # Phase 1 dry-run
  python scripts/audit_mac_mini.py --execute  # Phase 2 (requires prior approval)
  python scripts/audit_mac_mini.py --baseline # Phase 3 (run after Phase 2)
"""

import argparse
import asyncio
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import acreate_client

# Load .env from orchestrator root so script can be run standalone
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Configuration ────────────────────────────────────────────────────────────

WINGMEN_LAUNCHD_PREFIX = "dev.wingmen."
REPORTS_DIR = Path(__file__).parent.parent / "reports"
KILL_LOG_DIR = Path("/Users/musa/wingmen-logs")
SGT = ZoneInfo("Asia/Singapore")

# Processes matching these patterns (against full command string) are SAFE_KILL
# if they are NOT in the PROTECTED set.
SAFE_KILL_PATTERNS = [
    re.compile(r"node.*next.*dev", re.IGNORECASE),
    re.compile(r"node.*next-server", re.IGNORECASE),
    re.compile(r"code-server", re.IGNORECASE),
    re.compile(r"\.vscode-server", re.IGNORECASE),
    re.compile(r"(npm|pnpm|yarn).*dev\b", re.IGNORECASE),
    re.compile(r"webpack.*serve", re.IGNORECASE),
    re.compile(r"python.*manage\.py\s+runserver", re.IGNORECASE),
    re.compile(r"python.*uvicorn", re.IGNORECASE),
    re.compile(r"python.*gunicorn", re.IGNORECASE),
    re.compile(r"ruby.*(thin|puma)", re.IGNORECASE),
    re.compile(r"Simulator(Trampoline)?$"),
]

# Electron renderer heuristic: >500MB RSS, >1h old, command contains helper/renderer
ELECTRON_RSS_THRESHOLD_MB = 500
ELECTRON_AGE_THRESHOLD_SECONDS = 3600
ELECTRON_PATTERNS = [
    re.compile(r"Electron.*Helper", re.IGNORECASE),
    re.compile(r"renderer.*Electron", re.IGNORECASE),
]

# REVIEW threshold: >10% CPU or >1GB RSS
REVIEW_CPU_THRESHOLD = 10.0
REVIEW_RSS_THRESHOLD_MB = 1024

# Phase 3 verdict thresholds (CPU idle %)
VERDICT_SUFFICIENT = 70.0
VERDICT_MARGINAL_LOW = 40.0

# Jumuah window: Friday 11:00–15:00 SGT
JUMUAH_DAY = 4  # Friday (weekday 4 in Python)
JUMUAH_START_HOUR = 11
JUMUAH_END_HOUR = 15


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("audit_mac_mini")


# ── Supabase client ──────────────────────────────────────────────────────────

async def get_supabase():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return await acreate_client(url, key)


# ── Jumuah guard ─────────────────────────────────────────────────────────────

def assert_not_jumuah():
    now_sgt = datetime.datetime.now(tz=SGT)
    if now_sgt.weekday() == JUMUAH_DAY:
        if JUMUAH_START_HOUR <= now_sgt.hour < JUMUAH_END_HOUR:
            raise RuntimeError(
                f"Phase 2 blocked during Jumuah window "
                f"(Friday {JUMUAH_START_HOUR}:00–{JUMUAH_END_HOUR}:00 SGT). "
                f"Current SGT time: {now_sgt.strftime('%H:%M')}. "
                f"Run after {JUMUAH_END_HOUR}:00 SGT."
            )


# ── Process enumeration ──────────────────────────────────────────────────────

def get_all_processes() -> list[dict]:
    """Return list of process dicts from ps output."""
    result = subprocess.run(
        ["ps", "-Ao", "pid,ppid,user,pcpu,rss,etime,comm"],
        capture_output=True, text=True, check=True,
    )
    procs = []
    for line in result.stdout.strip().splitlines()[1:]:
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        try:
            procs.append({
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "user": parts[2],
                "cpu": float(parts[3]),
                "rss_mb": int(parts[4]) / 1024,
                "etime": parts[5],
                "command": parts[6].strip(),
            })
        except (ValueError, IndexError):
            continue
    return sorted(procs, key=lambda p: p["cpu"], reverse=True)


def get_protected_pids() -> set[int]:
    """
    Return PIDs of all dev.wingmen.* launchd services and their descendants.
    Also includes the current script's PID and its ancestors.
    """
    protected = set()

    # 1. Find PIDs of all dev.wingmen.* launchd jobs
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, check=True,
        )
        wingmen_pids = set()
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[2].startswith(WINGMEN_LAUNCHD_PREFIX):
                try:
                    pid = int(parts[0])
                    if pid > 0:
                        wingmen_pids.add(pid)
                except ValueError:
                    pass
        protected.update(wingmen_pids)

        # 2. Add all descendants of wingmen PIDs
        all_procs = get_all_processes()
        pid_to_ppid = {p["pid"]: p["ppid"] for p in all_procs}

        def get_descendants(parent_pid: int) -> set[int]:
            desc = set()
            for pid, ppid in pid_to_ppid.items():
                if ppid == parent_pid:
                    desc.add(pid)
                    desc.update(get_descendants(pid))
            return desc

        for wpid in list(wingmen_pids):
            protected.update(get_descendants(wpid))

    except subprocess.CalledProcessError as e:
        log.warning(f"launchctl list failed: {e}")

    # 3. Protect this script itself
    protected.add(os.getpid())

    return protected


def etime_to_seconds(etime: str) -> int:
    """Convert ps etime format (DD-HH:MM:SS or HH:MM:SS or MM:SS) to seconds."""
    try:
        if "-" in etime:
            days_part, rest = etime.split("-", 1)
            days = int(days_part)
        else:
            days, rest = 0, etime
        parts = rest.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            h, m, s = 0, int(parts[0]), int(parts[1])
        else:
            return 0
        return days * 86400 + h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return 0


def is_system_process(proc: dict) -> bool:
    """True for macOS system daemons and low-footprint system processes."""
    system_users = {"root", "_windowserver", "_mdnsresponder", "_spotlight",
                    "_networkd", "_coreaudiod", "_apsd", "_hidd"}
    if proc["user"] in system_users:
        return True
    system_patterns = [
        re.compile(r"^/(usr/)?sbin/", re.IGNORECASE),
        re.compile(r"^/System/", re.IGNORECASE),
        re.compile(r"^launchd$"),
        re.compile(r"^kernel_task$"),
    ]
    cmd = proc["command"]
    return any(p.search(cmd) for p in system_patterns)


def is_electron_safe_kill(proc: dict) -> bool:
    """Electron renderer >500MB RSS >1h old — but only if not PROTECTED (caller checks)."""
    cmd = proc["command"]
    if not any(p.search(cmd) for p in ELECTRON_PATTERNS):
        return False
    return (
        proc["rss_mb"] > ELECTRON_RSS_THRESHOLD_MB
        and etime_to_seconds(proc["etime"]) > ELECTRON_AGE_THRESHOLD_SECONDS
    )


def classify_processes(procs: list[dict], protected_pids: set[int]) -> dict:
    """
    Returns dict with keys: safe_kill, review, ignore, protected.
    Each is a list of process dicts with added 'reason' key.
    """
    buckets = {"protected": [], "safe_kill": [], "review": [], "ignore": []}

    for proc in procs:
        pid = proc["pid"]
        cmd = proc["command"]

        # Protected wins over everything
        if pid in protected_pids:
            proc["reason"] = "dev.wingmen.* launchd service or descendant"
            buckets["protected"].append(proc)
            continue

        # System processes → ignore
        if is_system_process(proc):
            proc["reason"] = "macOS system process"
            buckets["ignore"].append(proc)
            continue

        # Check SAFE_KILL patterns
        matched_pattern = None
        for pattern in SAFE_KILL_PATTERNS:
            if pattern.search(cmd):
                matched_pattern = pattern.pattern
                break

        if matched_pattern:
            # Guard: if parent is protected, child is also protected
            if proc["ppid"] in protected_pids:
                proc["reason"] = f"parent PID {proc['ppid']} is PROTECTED (child of wingmen service)"
                protected_pids.add(pid)  # extend protection to this child
                buckets["protected"].append(proc)
            else:
                proc["reason"] = f"matches SAFE_KILL pattern: {matched_pattern}"
                buckets["safe_kill"].append(proc)
            continue

        # Electron heuristic
        if is_electron_safe_kill(proc):
            if proc["ppid"] in protected_pids:
                proc["reason"] = f"Electron renderer but parent PID {proc['ppid']} is PROTECTED"
                protected_pids.add(pid)
                buckets["protected"].append(proc)
            else:
                proc["reason"] = f"Electron renderer >500MB RSS >1h old"
                buckets["safe_kill"].append(proc)
            continue

        # REVIEW: high CPU or high memory (non-system, non-safe-kill)
        if proc["cpu"] > REVIEW_CPU_THRESHOLD or proc["rss_mb"] > REVIEW_RSS_THRESHOLD_MB:
            reasons = []
            if proc["cpu"] > REVIEW_CPU_THRESHOLD:
                reasons.append(f"CPU {proc['cpu']:.1f}% > {REVIEW_CPU_THRESHOLD}%")
            if proc["rss_mb"] > REVIEW_RSS_THRESHOLD_MB:
                reasons.append(f"RSS {proc['rss_mb']:.0f}MB > {REVIEW_RSS_THRESHOLD_MB}MB")
            proc["reason"] = "; ".join(reasons)
            buckets["review"].append(proc)
            continue

        # Everything else → ignore
        proc["reason"] = "low resource, not matching any pattern"
        buckets["ignore"].append(proc)

    return buckets


# ── Report generation ─────────────────────────────────────────────────────────

def format_proc_table(procs: list[dict]) -> str:
    if not procs:
        return "  (none)\n"
    lines = [f"  {'PID':>7}  {'CPU%':>6}  {'RSS MB':>7}  {'ETIME':>10}  {'COMMAND':<50}  REASON"]
    lines.append("  " + "-" * 105)
    for p in procs:
        cmd = p["command"][:48]
        lines.append(
            f"  {p['pid']:>7}  {p['cpu']:>6.1f}  {p['rss_mb']:>7.0f}  {p['etime']:>10}  {cmd:<50}  {p['reason']}"
        )
    return "\n".join(lines) + "\n"


def write_report(buckets: dict, timestamp: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"mac-mini-audit-{timestamp}.md"

    lines = [
        f"# Mac Mini Process Audit — {timestamp}",
        "",
        f"Generated: {datetime.datetime.now(tz=SGT).isoformat()}",
        f"Protected PIDs (dev.wingmen.* services + descendants): "
        f"{len(buckets['protected'])} processes",
        "",
        "---",
        "",
        f"## SAFE_KILL ({len(buckets['safe_kill'])} processes)",
        "_These will be terminated in Phase 2 with --execute approval._",
        "",
        format_proc_table(buckets["safe_kill"]),
        "",
        f"## REVIEW ({len(buckets['review'])} processes)",
        "_High resource usage — human/CAI review required before any action._",
        "",
        format_proc_table(buckets["review"]),
        "",
        f"## PROTECTED ({len(buckets['protected'])} processes)",
        "_dev.wingmen.* launchd services and their children. Never touched._",
        "",
        format_proc_table(buckets["protected"]),
        "",
        f"## IGNORE ({len(buckets['ignore'])} processes)",
        "_macOS system processes and low-resource user apps. Omitted for brevity._",
        "",
        f"  ({len(buckets['ignore'])} processes in IGNORE bucket — not listed)",
        "",
        "---",
        "",
        "## Next Steps",
        "",
        "To execute Phase 2 kills on SAFE_KILL bucket:",
        "```",
        "python scripts/audit_mac_mini.py --execute",
        "```",
        "",
        "**Requires explicit approval via agent_messages reply to the CTO channel message**",
        "before --execute will proceed.",
    ]
    report_path.write_text("\n".join(lines))
    return report_path


# ── Agent messages posting ────────────────────────────────────────────────────

async def post_agent_message(supabase, subject: str, body: str, requires_response: bool = False) -> int:
    result = (
        await supabase.table("agent_messages")
        .insert({
            "from_agent": "cc-ihsanos",
            "to_agent": "cai",
            "message_type": "update",
            "subject": subject,
            "body": body,
            "requires_response": requires_response,
        })
        .execute()
    )
    msg_id = result.data[0]["id"]
    log.info(f"Posted agent_message id={msg_id}: {subject}")
    return msg_id


async def wait_for_approval(supabase, report_msg_id: int, timeout_seconds: int = 3600) -> bool:
    """
    Poll agent_messages for an approval reply referencing our report message.
    Returns True if approved, False if timed out.
    Approval is any message to_agent='cc-ihsanos' with body containing 'approve' or '--execute'.
    """
    log.info(f"Waiting for approval via agent_messages (timeout {timeout_seconds}s)...")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = (
            await supabase.table("agent_messages")
            .select("id, from_agent, body, created_at")
            .eq("to_agent", "cc-ihsanos")
            .is_("responded_at", None)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        for msg in result.data:
            body = (msg.get("body") or "").lower()
            if "approve" in body or "--execute" in body or "execute phase 2" in body:
                log.info(f"Approval received in message id={msg['id']} from {msg['from_agent']}")
                return True
        await asyncio.sleep(30)
    return False


# ── Phase 1: Discovery ────────────────────────────────────────────────────────

async def phase1(supabase):
    log.info("=== Phase 1: Process Discovery (dry-run) ===")
    timestamp = datetime.datetime.now(tz=SGT).strftime("%Y-%m-%d-%H%M")

    log.info("Enumerating processes...")
    procs = get_all_processes()
    log.info(f"Found {len(procs)} processes total")

    log.info("Building PROTECTED set from dev.wingmen.* launchd services...")
    protected_pids = get_protected_pids()
    log.info(f"PROTECTED: {len(protected_pids)} PIDs (services + descendants)")

    log.info("Classifying processes...")
    buckets = classify_processes(procs, protected_pids)

    log.info(
        f"Classification: SAFE_KILL={len(buckets['safe_kill'])} "
        f"REVIEW={len(buckets['review'])} "
        f"PROTECTED={len(buckets['protected'])} "
        f"IGNORE={len(buckets['ignore'])}"
    )

    report_path = write_report(buckets, timestamp)
    log.info(f"Report written: {report_path}")

    # Post to agent_messages
    safe_kill_summary = "\n".join(
        f"  PID {p['pid']}: {p['command'][:60]} ({p['reason']})"
        for p in buckets["safe_kill"]
    ) or "  (none)"

    review_summary = "\n".join(
        f"  PID {p['pid']}: {p['command'][:60]} ({p['reason']})"
        for p in buckets["review"]
    ) or "  (none)"

    body = (
        f"Phase 1 dry-run complete. Report: {report_path}\n\n"
        f"SAFE_KILL ({len(buckets['safe_kill'])}):\n{safe_kill_summary}\n\n"
        f"REVIEW ({len(buckets['review'])}) — human/CAI eyes needed:\n{review_summary}\n\n"
        f"PROTECTED: {len(buckets['protected'])} dev.wingmen.* processes — never touched.\n\n"
        f"To approve Phase 2 kills, reply with 'approve' or '--execute' to this message."
    )

    msg_id = await post_agent_message(
        supabase,
        subject=f"TASK-043 Phase 1 complete — {len(buckets['safe_kill'])} SAFE_KILL, {len(buckets['review'])} REVIEW",
        body=body,
        requires_response=True,
    )

    log.info(f"Halting. Waiting for approval via agent_messages (msg_id={msg_id}) or --execute flag.")
    log.info(f"Run: python scripts/audit_mac_mini.py --execute")
    return buckets, msg_id


# ── Phase 2: Execute ──────────────────────────────────────────────────────────

async def phase2(supabase):
    assert_not_jumuah()
    log.info("=== Phase 2: Execute SAFE_KILL ===")

    # Re-discover (state may have changed since Phase 1)
    log.info("Re-enumerating processes for fresh classification...")
    procs = get_all_processes()
    protected_pids = get_protected_pids()
    buckets = classify_processes(procs, protected_pids)

    safe_kill = buckets["safe_kill"]
    if not safe_kill:
        log.info("No SAFE_KILL processes found. Nothing to do.")
        return

    log.info(f"Will terminate {len(safe_kill)} SAFE_KILL processes.")

    KILL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    kill_log_path = KILL_LOG_DIR / f"mac-mini-cleanup-{datetime.datetime.now(tz=SGT).strftime('%Y-%m-%d')}.log"

    killed = []
    skipped = []

    for proc in safe_kill:
        pid = proc["pid"]

        # Final safety check: re-verify not in protected set
        if pid in protected_pids:
            log.warning(f"PID {pid} appeared in PROTECTED after re-check — skipping.")
            skipped.append(proc)
            continue

        # Check wingmen services haven't flapped
        try:
            wm_check = subprocess.run(
                ["launchctl", "list"],
                capture_output=True, text=True, timeout=5,
            )
            running_wingmen = [
                l for l in wm_check.stdout.splitlines()
                if WINGMEN_LAUNCHD_PREFIX in l and l.split()[0].lstrip("-").isdigit()
            ]
            if len(running_wingmen) < 2:  # sanity: at least orchestrator + one other
                log.error("wingmen services appear to have flapped — aborting Phase 2.")
                await post_agent_message(
                    supabase,
                    subject="TASK-043 Phase 2 ABORTED — wingmen service flap detected",
                    body=f"Aborted after {len(killed)} kills. wingmen launchd check returned fewer than 2 running services. Manual review required.",
                )
                break
        except Exception as e:
            log.warning(f"Watchdog check failed: {e} — continuing cautiously")

        cmd = proc["command"][:80]
        log.info(f"SIGTERM → PID {pid} ({cmd})")

        try:
            os.kill(pid, 15)  # SIGTERM
            # Log the action
            with open(kill_log_path, "a") as f:
                f.write(
                    f"{datetime.datetime.now(tz=SGT).isoformat()} SIGTERM pid={pid} "
                    f"cmd={cmd!r} reason={proc['reason']!r}\n"
                )

            # Wait 10s then SIGKILL if still alive
            await asyncio.sleep(10)
            try:
                os.kill(pid, 0)  # check if still alive
                log.info(f"PID {pid} still alive after SIGTERM — sending SIGKILL")
                os.kill(pid, 9)
                with open(kill_log_path, "a") as f:
                    f.write(
                        f"{datetime.datetime.now(tz=SGT).isoformat()} SIGKILL pid={pid} "
                        f"cmd={cmd!r} (did not exit after SIGTERM)\n"
                    )
            except ProcessLookupError:
                pass  # already gone after SIGTERM

            killed.append(proc)

        except ProcessLookupError:
            log.info(f"PID {pid} already gone — skipping")
            skipped.append(proc)
        except PermissionError:
            log.warning(f"Permission denied for PID {pid} — skipping")
            skipped.append(proc)

    await post_agent_message(
        supabase,
        subject=f"TASK-043 Phase 2 complete — {len(killed)} killed, {len(skipped)} skipped",
        body=(
            f"Phase 2 execution complete.\n"
            f"Killed: {len(killed)}\n"
            f"Skipped (already gone or permission denied): {len(skipped)}\n"
            f"Kill log: {kill_log_path}\n\n"
            f"Run Phase 3 to take baseline measurement:\n"
            f"  python scripts/audit_mac_mini.py --baseline"
        ),
    )
    log.info(f"Phase 2 complete. Kill log: {kill_log_path}")


# ── Phase 3: Baseline measurement ────────────────────────────────────────────

def sample_cpu() -> dict:
    """Sample current CPU stats via top."""
    try:
        result = subprocess.run(
            ["top", "-l", "1", "-n", "0"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "CPU usage" in line:
                # e.g. "CPU usage: 3.33% user, 5.55% sys, 91.11% idle"
                idle_match = re.search(r"(\d+\.?\d*)%\s+idle", line)
                user_match = re.search(r"(\d+\.?\d*)%\s+user", line)
                sys_match = re.search(r"(\d+\.?\d*)%\s+sys", line)
                return {
                    "idle": float(idle_match.group(1)) if idle_match else 0.0,
                    "user": float(user_match.group(1)) if user_match else 0.0,
                    "sys": float(sys_match.group(1)) if sys_match else 0.0,
                }
    except Exception as e:
        log.warning(f"CPU sample failed: {e}")
    return {"idle": 0.0, "user": 0.0, "sys": 0.0}


async def wait_for_idle_queue(supabase):
    """Wait until no jobs are actively running in the orchestrator queue."""
    log.info("Checking orchestrator job queue before starting baseline...")
    while True:
        result = (
            await supabase.table("jobs")
            .select("id")
            .eq("status", "running")
            .limit(1)
            .execute()
        )
        if not result.data:
            log.info("Queue idle — starting baseline timer.")
            return
        log.info(f"Job {result.data[0]['id']} still running — waiting 60s...")
        await asyncio.sleep(60)


async def phase3(supabase):
    log.info("=== Phase 3: Baseline Measurement (2-hour window) ===")

    await wait_for_idle_queue(supabase)

    samples = []
    duration_seconds = 7200  # 2 hours
    interval_seconds = 300   # 5 minutes
    n_samples = duration_seconds // interval_seconds

    log.info(f"Collecting {n_samples} samples over 2 hours (every 5 min)...")
    start_time = time.monotonic()

    for i in range(n_samples):
        sample = sample_cpu()
        sample["t"] = i * interval_seconds
        samples.append(sample)
        log.info(
            f"Sample {i+1}/{n_samples}: "
            f"idle={sample['idle']:.1f}% user={sample['user']:.1f}% sys={sample['sys']:.1f}%"
        )
        if i < n_samples - 1:
            await asyncio.sleep(interval_seconds)

    # Calculate averages
    avg_idle = sum(s["idle"] for s in samples) / len(samples)
    avg_user = sum(s["user"] for s in samples) / len(samples)
    avg_sys = sum(s["sys"] for s in samples) / len(samples)
    min_idle = min(s["idle"] for s in samples)
    max_idle = max(s["idle"] for s in samples)

    # Verdict
    if avg_idle >= VERDICT_SUFFICIENT:
        verdict = "INTEL_MINI_SUFFICIENT"
        verdict_detail = (
            f"Average idle {avg_idle:.1f}% >= {VERDICT_SUFFICIENT}%. "
            f"Keep Intel Mini. Cancel M1 purchase consideration."
        )
    elif avg_idle >= VERDICT_MARGINAL_LOW:
        verdict = "MARGINAL"
        verdict_detail = (
            f"Average idle {avg_idle:.1f}% between {VERDICT_MARGINAL_LOW}%–{VERDICT_SUFFICIENT}%. "
            f"Revisit after ARCH-030 cutover reduces load further."
        )
    else:
        verdict = "UPGRADE_JUSTIFIED"
        verdict_detail = (
            f"Average idle {avg_idle:.1f}% < {VERDICT_MARGINAL_LOW}%. "
            f"Proceed to secondhand M1 16GB/512GB."
        )

    # Write report
    timestamp = datetime.datetime.now(tz=SGT).strftime("%Y-%m-%d-%H%M")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"mac-mini-baseline-{timestamp}.md"

    sample_table = "\n".join(
        f"| {s['t']//60:3d}m | {s['idle']:5.1f}% | {s['user']:5.1f}% | {s['sys']:5.1f}% |"
        for s in samples
    )

    report_path.write_text(
        f"# Mac Mini Baseline Measurement — {timestamp}\n\n"
        f"Duration: 2 hours | Samples: {len(samples)} | Interval: 5 min\n\n"
        f"## Summary\n\n"
        f"| Metric | Value |\n|--------|-------|\n"
        f"| Avg idle | {avg_idle:.1f}% |\n"
        f"| Avg user | {avg_user:.1f}% |\n"
        f"| Avg sys | {avg_sys:.1f}% |\n"
        f"| Min idle | {min_idle:.1f}% |\n"
        f"| Max idle | {max_idle:.1f}% |\n\n"
        f"## Verdict: {verdict}\n\n{verdict_detail}\n\n"
        f"## Thresholds\n\n"
        f"- >={VERDICT_SUFFICIENT}% idle → INTEL_MINI_SUFFICIENT\n"
        f"- {VERDICT_MARGINAL_LOW}%–{VERDICT_SUFFICIENT}% idle → MARGINAL\n"
        f"- <{VERDICT_MARGINAL_LOW}% idle → UPGRADE_JUSTIFIED\n\n"
        f"## Sample Data\n\n"
        f"| Time | Idle | User | Sys |\n|------|------|------|-----|\n"
        f"{sample_table}\n"
    )

    log.info(f"Baseline report written: {report_path}")
    log.info(f"VERDICT: {verdict} — {verdict_detail}")

    await post_agent_message(
        supabase,
        subject=f"TASK-043 Phase 3 complete — Verdict: {verdict}",
        body=(
            f"Baseline measurement complete.\n\n"
            f"Avg idle: {avg_idle:.1f}% | Avg user: {avg_user:.1f}% | Avg sys: {avg_sys:.1f}%\n"
            f"Min idle: {min_idle:.1f}% | Max idle: {max_idle:.1f}%\n\n"
            f"VERDICT: {verdict}\n{verdict_detail}\n\n"
            f"Full report: {report_path}"
        ),
    )


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Mac Mini process audit + baseline measurement")
    parser.add_argument("--execute", action="store_true", help="Phase 2: execute SAFE_KILL (requires prior approval)")
    parser.add_argument("--baseline", action="store_true", help="Phase 3: 2-hour CPU baseline measurement")
    parser.add_argument("--no-approval-wait", action="store_true", help="Skip agent_messages approval poll (for testing)")
    args = parser.parse_args()

    supabase = await get_supabase()

    if args.execute:
        if not args.no_approval_wait:
            # Check that approval was given via agent_messages
            log.info("Checking agent_messages for prior approval...")
            result = (
                await supabase.table("agent_messages")
                .select("id, from_agent, body, created_at")
                .eq("to_agent", "cc-ihsanos")
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
            approved = any(
                "approve" in (m.get("body") or "").lower()
                or "--execute" in (m.get("body") or "").lower()
                or "execute phase 2" in (m.get("body") or "").lower()
                for m in result.data
            )
            if not approved:
                log.error(
                    "No approval found in recent agent_messages. "
                    "Post an approval reply to the Phase 1 report message, "
                    "or use --no-approval-wait to bypass (testing only)."
                )
                sys.exit(1)
        await phase2(supabase)
    elif args.baseline:
        await phase3(supabase)
    else:
        await phase1(supabase)


if __name__ == "__main__":
    asyncio.run(main())
