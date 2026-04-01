"""memory_sync — midnight diff of latest two snapshots, flag significant changes."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("wingmen.nervous_system.memory_sync")


async def memory_sync(supabase):
    """Diff latest two snapshots and log significant changes."""
    start = time.monotonic()
    logger.info("memory_sync starting...")

    try:
        result = await supabase.table("wingmen_brain").select("*").order("snapshot_at", desc=True).limit(2).execute()
        snapshots = result.data or []

        if len(snapshots) < 2:
            logger.info("memory_sync: fewer than 2 snapshots — nothing to diff")
            return

        current = snapshots[0]
        previous = snapshots[1]

        changes = []

        # Compare repos
        current_repos = {r["name"]: r for r in current.get("repos", [])}
        previous_repos = {r["name"]: r for r in previous.get("repos", [])}

        # New repos
        for name in current_repos:
            if name not in previous_repos:
                changes.append(f"New repo detected: {name}")

        # Removed repos
        for name in previous_repos:
            if name not in current_repos:
                changes.append(f"Repo removed: {name}")

        # Health changes
        for name, curr in current_repos.items():
            prev = previous_repos.get(name, {})
            if curr.get("health") != prev.get("health"):
                changes.append(f"{name}: health {prev.get('health', '?')} → {curr.get('health', '?')}")
            if curr.get("blockers") and not prev.get("blockers"):
                changes.append(f"{name}: new blockers — {', '.join(curr['blockers'][:3])}")

        # Client changes
        current_clients = {c["name"] for c in current.get("clients", [])}
        previous_clients = {c["name"] for c in previous.get("clients", [])}
        for name in current_clients - previous_clients:
            changes.append(f"New client: {name}")
        for name in previous_clients - current_clients:
            changes.append(f"Client removed: {name}")

        # Sync health change
        if current.get("sync_health") != previous.get("sync_health"):
            changes.append(f"Sync health: {previous.get('sync_health', '?')} → {current.get('sync_health', '?')}")

        # Log results
        duration = int((time.monotonic() - start) * 1000)
        details = "\n".join(changes) if changes else "No significant changes"

        await supabase.table("brain_sync_log").insert({
            "task_name": "memory_sync",
            "status": "success",
            "duration_ms": duration,
            "details": details,
        }).execute()

        if changes:
            logger.info(f"memory_sync: {len(changes)} changes detected")
            for c in changes:
                logger.info(f"  → {c}")

            # Write MEMORY_SYNC_LOG.md for claude.ai context
            import os
            from pathlib import Path
            from datetime import datetime, timezone
            log_path = Path(os.path.expanduser("~/wingmen/MEMORY_SYNC_LOG.md"))
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            existing = log_path.read_text() if log_path.exists() else "# Memory Sync Log\n\n"
            entry = f"## {now}\n" + "\n".join(f"- {c}" for c in changes) + "\n\n"
            # Prepend new entry after header
            header, _, body = existing.partition("\n\n")
            log_path.write_text(f"{header}\n\n{entry}{body}")
        else:
            logger.info("memory_sync: no significant changes")

    except Exception as e:
        logger.error(f"memory_sync failed: {e}")
        try:
            await supabase.table("brain_sync_log").insert({
                "task_name": "memory_sync", "status": "failed", "details": str(e)[:500],
            }).execute()
        except Exception:
            pass
