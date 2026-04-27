"""orch_self_audit — defense funnel for the orchestrator's own infrastructure.

Per Musa's directive (this session): "constantly self audit and self heal with
relevant testing. you should get the same defense funnels as ihsanos."

ihsanos has cryptographic hash-chain audit_log + Hard Constraints + per-write
triggers. Orchestrator equivalent is governance integrity: provenance trails
(BUG-024 Phase 1, BUG-032), bridge audit-log (CAI-RESP-091), and now this
runtime-audit module.

Three first-line audits run alongside agent_watchdog every ~10-min tick:

1. **writer_health** — has `repo_context_writer` produced a successful row in
   the last 60 min? (4× the 15-min cadence). Catches silent-failure modes
   like the Gap 3 schema mismatch I shipped this session — would have fired
   within 75 min instead of being caught by manual log-grep.

2. **bridge_tier3_volume** — count of `notification_log` rows with
   `source='bridge_tier3_misroute'` in the last 24h. Threshold: 3 firings/24h.
   Above threshold signals CAI persona-discipline drift on
   CAI-PROCESS-ROUTING-001 (populate announce_to_agent at INSERT). Below
   threshold = healthy; threshold-crossings get one alert per 24h bucket.

3. **migration_consistency** — for each .sql file in supabase/migrations/,
   verify a corresponding row exists in supabase_migrations.schema_migrations
   keyed on the file's `name` (decision_ref-style suffix). Files-without-rows
   signal an unapplied migration on main; drift between source-control and DB.

All audits dedup via notification_log dedup_key on hour bucket so threshold
crossings don't spam Musa's Telegram. Source/recipient pattern matches
agent_watchdog precedent (CAI-RESP-091 routing for bridge_tier3 audit log).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from nervous_system import error_tracker

logger = logging.getLogger("wingmen.orch_self_audit")

# Threshold knobs — keep in module so test fixture overrides work cleanly.
_WRITER_STALE_MINUTES = 60         # 4× the 15-min repo_context_writer cadence
_TIER3_VOLUME_THRESHOLD = 3        # firings per 24h before alert
_MIGRATIONS_DIR = Path(__file__).parent.parent / "supabase" / "migrations"


async def run_orch_audit(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Run all three audit checks. Fail-isolated: each check's exception is
    logged + tracked but does not block subsequent checks."""
    for check, name in (
        (_audit_writer_health, "writer_health"),
        (_audit_bridge_tier3_volume, "bridge_tier3_volume"),
        (_audit_migration_consistency, "migration_consistency"),
    ):
        try:
            await check(supabase, bot, musa_chat_id)
        except Exception as e:
            logger.error(f"orch_self_audit.{name} failed: {e}")
            error_tracker.track_exception(f"orch_self_audit.{name}", e)


# ----------------------------------------------------------------------------
# Audit 1 — writer_health: repo_context staleness
# ----------------------------------------------------------------------------

async def _audit_writer_health(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Alert if no repo_context row updated in the last 60 min — the writer
    has either silent-failed or stopped firing. Catches Gap-3-class silent
    failures within one audit cycle."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=_WRITER_STALE_MINUTES)).isoformat()

    result = await supabase.table("repo_context").select(
        "repo, updated_at"
    ).order("updated_at", desc=True).limit(1).execute()
    rows = result.data or []
    if not rows:
        # Empty table: writer has never run. Don't alert here — the
        # check_repo_context_health early-return in agent_watchdog covers this.
        return

    most_recent = rows[0].get("updated_at")
    if not most_recent or most_recent >= cutoff:
        return  # Fresh.

    try:
        last_dt = datetime.fromisoformat(most_recent.replace("Z", "+00:00"))
        stale_minutes = int((now - last_dt).total_seconds() / 60)
    except ValueError:
        stale_minutes = None

    dedup_key = f"orch_self_audit:writer_stale:{_dedup_bucket(now)}"
    if await _check_dedup(supabase, dedup_key):
        return

    msg = (
        f"⚠️ orch_self_audit: repo_context_writer stale\n\n"
        f"No repo_context row updated in {stale_minutes or 'unknown'} min "
        f"(threshold {_WRITER_STALE_MINUTES} min).\n"
        f"Most recent: {most_recent}\n\n"
        f"Likely silent-failure in nervous_system/repo_context_writer. "
        f"Inspect orch.log for 'repo_context_writer' entries; "
        f"check schema vs writer payload alignment."
    )
    await _send_and_log(
        supabase,
        bot=bot, musa_chat_id=musa_chat_id, msg=msg,
        source="orch_self_audit.writer_stale",
        decision_ref="ORCH-SELF-AUDIT", dedup_key=dedup_key,
    )


# ----------------------------------------------------------------------------
# Audit 2 — bridge_tier3_volume anomaly
# ----------------------------------------------------------------------------

async def _audit_bridge_tier3_volume(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Alert if Tier-3 fallback fired more than threshold times in last 24h.
    Each firing = a strategic_decisions filing without announce_to_agent or
    parent_msg_id; per CAI-PROCESS-ROUTING-001 these should be ZERO going
    forward. Volume above threshold signals discipline drift back into the
    Tier-3-default-routes-to-cc-ihsanos noise pattern."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()

    result = await supabase.table("notification_log").select(
        "id"
    ).eq("source", "bridge_tier3_misroute").gte("created_at", cutoff).execute()
    count = len(result.data or [])
    if count <= _TIER3_VOLUME_THRESHOLD:
        return

    dedup_key = f"orch_self_audit:tier3_volume:{_dedup_bucket(now)}"
    if await _check_dedup(supabase, dedup_key):
        return

    msg = (
        f"⚠️ orch_self_audit: Tier-3 fallback volume above threshold\n\n"
        f"{count} bridge_tier3_misroute firings in last 24h "
        f"(threshold {_TIER3_VOLUME_THRESHOLD}).\n\n"
        f"CAI persona-discipline drift on CAI-PROCESS-ROUTING-001. "
        f"strategic_decisions are being filed without announce_to_agent or "
        f"parent_msg_id, falling through to cc-ihsanos legacy default. "
        f"Query notification_log WHERE source='bridge_tier3_misroute' "
        f"in last 24h for offending decision_refs."
    )
    await _send_and_log(
        supabase,
        bot=bot, musa_chat_id=musa_chat_id, msg=msg,
        source="orch_self_audit.tier3_volume",
        decision_ref="CAI-PROCESS-ROUTING-001", dedup_key=dedup_key,
    )


# ----------------------------------------------------------------------------
# Audit 3 — migration consistency
# ----------------------------------------------------------------------------

# Pattern: 20260427_bug033_restore_base_agent_id_not_null.sql
#                    └ name part used to match schema_migrations.name
_MIGRATION_FILENAME_RE = re.compile(r"^\d{8,}_(?P<name>.+)\.sql$")


async def _audit_migration_consistency(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Alert if a .sql file in supabase/migrations/ has no corresponding row
    in supabase_migrations.schema_migrations matching by `name`. Catches
    ‘merged to main but never applied’ drift."""
    if not _MIGRATIONS_DIR.is_dir():
        logger.warning(f"orch_self_audit: {_MIGRATIONS_DIR} not a directory; skipping")
        return

    # Build set of file-names (matched-via-regex) from local main branch.
    file_names: set[str] = set()
    for path in _MIGRATIONS_DIR.glob("*.sql"):
        m = _MIGRATION_FILENAME_RE.match(path.name)
        if m:
            file_names.add(m.group("name"))

    if not file_names:
        return

    # supabase_migrations.schema_migrations is in supabase_migrations schema,
    # not public — supabase-py's table() targets public by default. Use
    # postgrest's `.schema()` selector if available.
    try:
        result = await supabase.schema("supabase_migrations").table(
            "schema_migrations"
        ).select("name").execute()
    except AttributeError as e:
        # supabase-py version lacks .schema() — skip silently rather than fail.
        logger.debug(f"orch_self_audit.migration_consistency: .schema() unavailable: {e}")
        return

    db_names: set[str] = {r["name"] for r in (result.data or []) if r.get("name")}
    missing = sorted(file_names - db_names)
    if not missing:
        return

    dedup_key = f"orch_self_audit:migration_drift:{_dedup_bucket(now := datetime.now(timezone.utc))}"
    if await _check_dedup(supabase, dedup_key):
        return

    msg = (
        f"⚠️ orch_self_audit: migration drift detected\n\n"
        f"{len(missing)} .sql file(s) in supabase/migrations/ have no "
        f"matching row in schema_migrations:\n"
        + "\n".join(f"  - {n}" for n in missing[:10])
        + (f"\n  (+{len(missing) - 10} more)" if len(missing) > 10 else "")
        + "\n\nMigration may have been merged to main but never applied to "
          "remote Supabase. Apply via psycopg + record in schema_migrations, "
          "or remove the file if intentionally orphaned."
    )
    await _send_and_log(
        supabase,
        bot=bot, musa_chat_id=musa_chat_id, msg=msg,
        source="orch_self_audit.migration_drift",
        decision_ref="ORCH-SELF-AUDIT", dedup_key=dedup_key,
    )


# ----------------------------------------------------------------------------
# Helpers — same shape as agent_watchdog's helpers
# ----------------------------------------------------------------------------

def _dedup_bucket(now: datetime) -> str:
    """Hour-granularity dedup bucket — caps any audit alert at one Telegram
    fire per hour per bucket key."""
    return now.strftime("%Y-%m-%dT%H")


async def _check_dedup(supabase, dedup_key: str) -> bool:
    """Return True if we already sent an alert with this dedup_key."""
    try:
        existing = (
            await supabase.table("notification_log")
            .select("id")
            .eq("dedup_key", dedup_key)
            .limit(1)
            .execute()
        )
        return bool(existing.data)
    except Exception as e:
        logger.warning(f"orch_self_audit dedup check failed for {dedup_key}: {e}")
        error_tracker.track_exception("orch_self_audit.dedup_check", e)
        return False  # Fail open — attempt notification.


async def _send_and_log(
    supabase,
    *,
    bot,
    musa_chat_id: str | None,
    msg: str,
    source: str,
    decision_ref: str,
    dedup_key: str,
) -> None:
    """Send Telegram + log to notification_log. Mirrors agent_watchdog._send_and_log
    so the audit and watchdog channels look identical to the operator."""
    telegram_msg_id: int | None = None
    if bot and musa_chat_id:
        try:
            sent = await bot.send_message(chat_id=musa_chat_id, text=msg)
            telegram_msg_id = sent.message_id
            logger.info(f"orch_self_audit alert sent: {dedup_key}")
        except Exception as e:
            logger.error(f"orch_self_audit Telegram send failed: {e}")
            error_tracker.track_exception("orch_self_audit.telegram_send", e)
            return  # Don't log dedup if send failed.
    else:
        logger.info(f"orch_self_audit alert (no bot): {dedup_key}\n{msg}")

    try:
        await supabase.table("notification_log").insert({
            "source": source,
            "decision_ref": decision_ref[:100],
            "channel": "telegram",
            "recipient": musa_chat_id or "unknown",
            "message_text": msg,
            "telegram_msg_id": telegram_msg_id,
            "dedup_key": dedup_key,
        }).execute()
    except Exception as e:
        logger.error(f"orch_self_audit notification_log insert failed: {e}")
        error_tracker.track_exception("orch_self_audit.log_alert", e)
