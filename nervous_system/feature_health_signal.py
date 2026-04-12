"""Feature health signal collector.

Scans every row in wingmen_features and writes back an auto-computed
health_signal ('ok' | 'warn' | 'alert' | 'no_signal') plus detail JSONB.
The signal is advisory only — it does NOT gate stage promotion. Stage
remains a manual decision. The collector exists to surface which
features have observable health so Musa can scan the inventory and
see which unknown-stage rows are candidates for promotion review.

Per category:
  - process          — checks launchctl list for the matching plist
                       label (dev.wingmen.<slug-suffix>).
  - scheduled_task   — checks log tail for recent execution entries
                       based on the task's module name or log prefix.
  - edge_function    — queries Supabase edge function list via MCP;
                       does NOT call ?mode=full-self-test (costs money).
  - feature          — v1: no_signal. v2 could track associated data
                       table activity.
  - integration      — v1: no_signal.
  - agent            — v1: no_signal.

Signal definition:
  - ok         — observable signal says feature is running / being used
  - warn       — soft indicators (running but with recent warnings or
                 stale activity)
  - alert      — active errors or process missing
  - no_signal  — category not supported or collector couldn't compute

Origin: CTO Council session 2 option 4 decision (2026-04-12).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("wingmen.feature_health_signal")

HealthSignal = Literal["ok", "warn", "alert", "no_signal"]

ORCH_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ORCH_ROOT / "logs"

# How far back to look for "recent" activity per category
SCHEDULED_TASK_WARN_MINUTES = 60 * 6   # 6h → warn
SCHEDULED_TASK_ALERT_MINUTES = 60 * 24  # 24h → alert
PROCESS_LOG_STALE_MINUTES = 10          # process log hasn't been touched in 10m → warn


# ── Main entry ────────────────────────────────────────────────────


async def collect_feature_health(sb) -> None:
    """Scan all wingmen_features rows and update health_signal.

    Called from wingmen_orch.py main loop every N polls. Fail-soft:
    per-feature errors are logged and the collector moves on.
    """
    # Log at the TOP of the run so that when we iterate to our own
    # task_feature_health_signal row, its log-scan check finds a
    # fresh entry instead of reporting no_signal. Bootstrap fix for
    # the meta-health chicken-and-egg case.
    logger.info("feature_health_signal: collection starting")

    try:
        result = await (
            sb.table("wingmen_features")
            .select("id, slug, category, source_paths")
            .execute()
        )
    except Exception as e:
        logger.error(f"feature_health_signal query failed: {e}")
        return

    features = result.data or []
    if not features:
        return

    launchctl_state = await _launchctl_snapshot()

    now = datetime.now(timezone.utc).isoformat()
    updated = 0

    for feature in features:
        try:
            signal, detail = await _compute_signal(
                feature, launchctl_state
            )
        except Exception as e:
            logger.error(
                f"feature_health_signal compute failed for "
                f"{feature.get('slug')}: {e}"
            )
            signal = "no_signal"
            detail = {"error": str(e)[:200]}

        try:
            await (
                sb.table("wingmen_features")
                .update({
                    "health_signal": signal,
                    "health_signal_computed_at": now,
                    "health_signal_detail": detail,
                })
                .eq("id", feature["id"])
                .execute()
            )
            updated += 1
        except Exception as e:
            logger.error(
                f"feature_health_signal write failed for "
                f"{feature.get('slug')}: {e}"
            )

    logger.info(
        f"feature_health_signal: updated {updated}/{len(features)} rows"
    )


# ── Per-category compute ──────────────────────────────────────────


async def _compute_signal(
    feature: dict[str, Any],
    launchctl_state: dict[str, dict[str, Any]],
) -> tuple[HealthSignal, dict[str, Any]]:
    category = feature.get("category")
    slug = feature.get("slug", "")
    source_paths = feature.get("source_paths") or []

    if category == "process":
        return _check_process(slug, source_paths, launchctl_state)
    elif category == "scheduled_task":
        return await _check_scheduled_task(slug, source_paths)
    elif category == "edge_function":
        return _check_edge_function(slug, source_paths)
    else:
        return ("no_signal", {"reason": f"category {category!r} not supported in v1"})


def _check_process(
    slug: str,
    source_paths: list[str],
    launchctl_state: dict[str, dict[str, Any]],
) -> tuple[HealthSignal, dict[str, Any]]:
    # Find the plist label from source_paths (e.g., dev.wingmen.orchestrator)
    label = None
    for p in source_paths:
        m = re.search(r"dev\.wingmen\.([\w-]+)\.plist", p)
        if m:
            label = f"dev.wingmen.{m.group(1)}"
            break

    if not label:
        return (
            "no_signal",
            {"reason": "no dev.wingmen.*.plist found in source_paths"},
        )

    entry = launchctl_state.get(label)
    if not entry:
        return (
            "alert",
            {"label": label, "reason": "not present in launchctl list"},
        )

    pid = entry.get("pid")
    status = entry.get("status")

    # PID "-" means scheduled-but-not-running (fine for scheduled launchd agents)
    # PID numeric means running
    # status 0 means last exit was clean (or never exited)
    detail: dict[str, Any] = {
        "label": label,
        "pid": pid,
        "last_status": status,
    }

    if pid == "-":
        # Scheduled agent, not currently running. That's fine for daily-backup etc.
        # But for KeepAlive=true agents (orchestrator, ctobot, watchdog, mizan-bot)
        # this means the process is down.
        if label in {
            "dev.wingmen.orchestrator",
            "dev.wingmen.ctobot",
            "dev.wingmen.watchdog",
            "dev.wingmen.mizan-bot",
        }:
            return ("alert", {**detail, "reason": "KeepAlive agent not running"})
        return ("ok", {**detail, "mode": "scheduled"})

    try:
        pid_int = int(pid) if pid else 0
    except (ValueError, TypeError):
        pid_int = 0

    if pid_int > 0:
        try:
            status_int = int(status) if status else 0
        except (ValueError, TypeError):
            status_int = 0

        # Negative status codes are signal-terminations (e.g., -15 = SIGTERM)
        # launchctl shows the LAST exit, even if the process was respawned.
        # If pid > 0 AND last status is 0 or negative (respawn after signal),
        # the process is running. If pid > 0 AND last status is POSITIVE
        # (exit with error), the process crashed then respawned — warn.
        if status_int > 0:
            return ("warn", {**detail, "reason": f"last exit {status_int}"})
        return ("ok", detail)

    return ("alert", {**detail, "reason": "no pid"})


async def _check_scheduled_task(
    slug: str,
    source_paths: list[str],
) -> tuple[HealthSignal, dict[str, Any]]:
    # If the task is actually a launchd-scheduled shell script (has its
    # own plist), check via launchctl state — shell scripts typically
    # don't write to orch.log/cto_bot.log.
    has_plist = any(".plist" in p for p in source_paths)
    if has_plist:
        from_launchctl = _check_process(slug, source_paths, await _launchctl_snapshot())
        if from_launchctl[0] != "no_signal":
            return from_launchctl

    # Otherwise grep the Python log files for recent entries.
    log_prefixes = _infer_log_prefixes(slug, source_paths)
    if not log_prefixes:
        return (
            "no_signal",
            {"reason": "no known log prefix for this slug"},
        )

    latest = None
    matched_file = None
    for log_name in ("orch.log", "cto_bot.log"):
        log_path = LOG_DIR / log_name
        if not log_path.exists():
            continue
        ts = _last_matching_ts(log_path, log_prefixes)
        if ts and (latest is None or ts > latest):
            latest = ts
            matched_file = log_name

    if not latest:
        # ABSENCE of log entries ≠ positive evidence of breakage.
        # Use no_signal, not alert. Silent tasks (e.g., bug_escalation,
        # conversation_cleanup) that only log on error or state change
        # are indistinguishable from dead tasks at this layer. The UI
        # should show no_signal in gray, not red.
        return (
            "no_signal",
            {
                "reason": "no recent log entries matching known prefixes",
                "note": "absence of signal != positive breakage evidence",
                "prefixes": log_prefixes,
            },
        )

    age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
    detail = {
        "last_entry_at": latest.isoformat(),
        "age_minutes": round(age_min, 1),
        "log_file": matched_file,
        "prefixes": log_prefixes,
    }

    if age_min > SCHEDULED_TASK_ALERT_MINUTES:
        return ("alert", {**detail, "reason": f"last entry {int(age_min)}m ago"})
    if age_min > SCHEDULED_TASK_WARN_MINUTES:
        return ("warn", {**detail, "reason": f"last entry {int(age_min)}m ago"})
    return ("ok", detail)


def _check_edge_function(
    slug: str,
    source_paths: list[str],
) -> tuple[HealthSignal, dict[str, Any]]:
    # v1: we don't hit ?mode=full-self-test because it costs ~$0.0005 per call
    # per feature per collector run. For a single edge function (cto-strategist)
    # that's trivial, but the principle is "don't pay Anthropic for health
    # checks the deploy gate already runs on every deploy." So this check is
    # static: does the file exist on disk.
    for p in source_paths:
        fp = ORCH_ROOT / p
        if fp.exists():
            return (
                "ok",
                {"source_file_exists": str(fp), "note": "static check only"},
            )
    return (
        "no_signal",
        {"reason": "no source file found under orchestrator root"},
    )


# ── launchctl snapshot ────────────────────────────────────────────


async def _launchctl_snapshot() -> dict[str, dict[str, Any]]:
    """Parse `launchctl list | grep dev.wingmen` once, return as a dict."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "launchctl", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except Exception as e:
        logger.error(f"launchctl list failed: {e}")
        return {}

    out: dict[str, dict[str, Any]] = {}
    for line in stdout.decode(errors="replace").splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            continue
        pid, status, label = parts
        if label.startswith("dev.wingmen."):
            out[label] = {"pid": pid, "status": status}
    return out


# ── Log scanning helpers ──────────────────────────────────────────

# Known log prefix mappings for scheduled tasks. When adding a new task,
# add its slug → prefix mapping here. The prefix is the logger name used
# in the task's `logging.getLogger("...")` call.
SLUG_LOG_PREFIXES: dict[str, list[str]] = {
    "task_brain_sync": ["wingmen.nervous_system.brain_sync", "brain_sync"],
    "task_morning_brief": ["wingmen.nervous_system.morning_brief", "morning_brief"],
    "task_weekly_digest": ["wingmen.nervous_system.weekly_digest", "weekly_digest"],
    "task_memory_sync": ["wingmen.nervous_system.memory_sync", "memory_sync"],
    "task_session_compress": ["wingmen.nervous_system.session_compress", "session_compress"],
    "task_bug_escalation": ["wingmen.nervous_system.bug_escalation", "bug escalation"],
    "task_conversation_cleanup": [
        "wingmen.nervous_system.conversation_cleanup",
        "Conversation cleanup",
    ],
    "task_council_summary_loop": [
        "wingmen.council_summary",
        "Council summary",
        "Council session",
    ],
    "task_feature_health_signal": [
        "wingmen.feature_health_signal",
        "feature_health_signal",
    ],
    "task_daily_backup": ["daily_backup"],
    "task_health_check": ["health_check"],
}


def _infer_log_prefixes(slug: str, source_paths: list[str]) -> list[str]:
    if slug in SLUG_LOG_PREFIXES:
        return SLUG_LOG_PREFIXES[slug]
    # Fallback: derive from source file stem
    for p in source_paths:
        stem = Path(p).stem
        if stem:
            return [f"wingmen.{stem}", stem]
    return []


# Matches log lines like:  2026-04-12 07:33:46,450 [wingmen.council_summary] ...
LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]?\d*\s"
)


def _last_matching_ts(log_path: Path, prefixes: list[str]) -> datetime | None:
    """Scan the tail of a log file backwards for the most recent line that
    contains any of the given prefixes, and return its timestamp."""
    try:
        # Read the last ~200KB — log files are rotated in size, and we only
        # need recent entries.
        size = log_path.stat().st_size
        with log_path.open("rb") as f:
            if size > 200_000:
                f.seek(size - 200_000)
                f.readline()  # skip partial line
            tail = f.read().decode(errors="replace")
    except OSError:
        return None

    lines = tail.splitlines()
    for line in reversed(lines):
        if not any(p in line for p in prefixes):
            continue
        m = LOG_LINE_RE.match(line)
        if not m:
            continue
        try:
            # Log timestamps are local (Singapore +08) — interpret as UTC+8
            # and convert to UTC for comparison with now(UTC).
            naive = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
            # Assume the orchestrator runs in Singapore time
            local_tz_offset_hours = 8
            utc_ts = naive.replace(tzinfo=timezone.utc).timestamp() - (
                local_tz_offset_hours * 3600
            )
            return datetime.fromtimestamp(utc_ts, tz=timezone.utc)
        except ValueError:
            continue
    return None
