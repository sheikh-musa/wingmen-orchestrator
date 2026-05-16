"""autonomous_loop_detector — filesystem scan for high-frequency CC loops.

Per CAI-RESP-157 [A]. Scans ~/.claude/projects/<cc-family-dir>/*.jsonl
mtimes, counts sessions in last 24h, computes inter-arrival cadence, and
upserts flagged rows into active_autonomous_loops. Rows DELETE when the
loop quiets (sessions_24h <= threshold).

Visibility-only — auto-kill is CAI-RESP-157 [B] (separate module).
Detection threshold ratified at 50 sessions/24h per CAI-RESP-157 Q2 (well
below the empirical 299/24h cc-scholar burn floor; leaves headroom for
legitimate ralphy/soak/paused-jobs loops to operate without spurious flag).

Per CAI-RESP-157 constraint: must respect feedback_autonomous_loop_scope —
ralphy/soak/paused-jobs operating under-threshold are NOT flagged.
"""
from __future__ import annotations

import logging
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("wingmen.autonomous_loop_detector")


DETECTION_THRESHOLD_SESSIONS_24H = 50
_LOOKBACK_SECONDS = 24 * 3600

# Map ~/.claude/projects/ subdirectory → cc-family identity.
_DIR_TO_CC = {
    "-Users-sheikhmusa-wingmen-orchestrator": "cc-orchestrator",
    "-Users-sheikhmusa-wingmen-projects-ai-scholar": "cc-scholar",
    "-Users-sheikhmusa-wingmen-projects-cosem-tdu": "cc-cosem",
    "-Users-sheikhmusa-wingmen-projects-ihsanos": "cc-ihsanos",
}


def scan_one_directory(directory: Path) -> dict[str, Any]:
    """Scan a single CC project directory for recent .jsonl files.

    Returns {sessions_24h, cadence_seconds, last_fire_at} where:
      - sessions_24h: int count of .jsonl files mtime'd in last 24h
      - cadence_seconds: int median inter-arrival in seconds (None if <2 sessions)
      - last_fire_at: datetime of most recent jsonl, or None
    """
    now = time.time()
    cutoff = now - _LOOKBACK_SECONDS

    mtimes: list[float] = []
    if directory.exists():
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.endswith(".jsonl"):
                continue
            if entry.name.startswith("."):
                continue
            try:
                mt = entry.stat().st_mtime
            except OSError:
                continue
            if mt >= cutoff:
                mtimes.append(mt)

    sessions_24h = len(mtimes)
    if sessions_24h == 0:
        return {
            "sessions_24h": 0,
            "cadence_seconds": None,
            "last_fire_at": None,
        }

    mtimes.sort()
    last_fire_at = datetime.fromtimestamp(mtimes[-1], tz=timezone.utc)

    cadence_seconds: int | None = None
    if len(mtimes) >= 2:
        gaps = [mtimes[i + 1] - mtimes[i] for i in range(len(mtimes) - 1)]
        cadence_seconds = int(statistics.median(gaps))

    return {
        "sessions_24h": sessions_24h,
        "cadence_seconds": cadence_seconds,
        "last_fire_at": last_fire_at,
    }


def detect_active_loops(
    claude_projects_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan all known CC project directories; return flagged rows.

    A row is flagged when sessions_24h > DETECTION_THRESHOLD_SESSIONS_24H.

    Returns list of dicts: {cc_identity, last_fire_at, sessions_24h, cadence_seconds}.
    Unknown directories are skipped silently.
    """
    if claude_projects_root is None:
        claude_projects_root = Path.home() / ".claude" / "projects"

    flagged: list[dict[str, Any]] = []
    for cc_dir_name, cc_identity in _DIR_TO_CC.items():
        cc_dir = claude_projects_root / cc_dir_name
        scan = scan_one_directory(cc_dir)
        if scan["sessions_24h"] > DETECTION_THRESHOLD_SESSIONS_24H:
            flagged.append({
                "cc_identity": cc_identity,
                "last_fire_at": scan["last_fire_at"],
                "sessions_24h": scan["sessions_24h"],
                "cadence_seconds": scan["cadence_seconds"],
            })
    return flagged


async def sweep_active_autonomous_loops(supabase) -> None:
    """Detector sweep entry point — called from wingmen_orch main loop.

    Computes current flagged set; upserts flagged rows; deletes rows for CC
    families that have quieted below threshold.
    """
    try:
        flagged = detect_active_loops()
        flagged_identities = {r["cc_identity"] for r in flagged}

        # Upsert flagged rows
        for row in flagged:
            await (
                supabase.table("active_autonomous_loops")
                .upsert({
                    "cc_identity":     row["cc_identity"],
                    "last_fire_at":    row["last_fire_at"].isoformat(),
                    "sessions_24h":    row["sessions_24h"],
                    "cadence_seconds": row["cadence_seconds"],
                    "detected_at":     datetime.now(timezone.utc).isoformat(),
                })
                .execute()
            )

        # Delete rows for CCs no longer flagged
        result = await supabase.table("active_autonomous_loops").select("cc_identity").execute()
        existing = {r["cc_identity"] for r in (result.data or [])}
        for cc in existing - flagged_identities:
            await (
                supabase.table("active_autonomous_loops")
                .delete()
                .eq("cc_identity", cc)
                .execute()
            )
            logger.info(
                f"active_autonomous_loops: {cc} quieted below threshold; row deleted"
            )

        if flagged:
            logger.warning(
                f"active_autonomous_loops: {len(flagged)} CC families flagged: "
                + ", ".join(
                    f"{r['cc_identity']}({r['sessions_24h']}/24h)" for r in flagged
                )
            )
    except Exception as e:
        logger.error(f"sweep_active_autonomous_loops failed: {e}")
