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
from nervous_system.alert_format import format_alert

logger = logging.getLogger("wingmen.orch_self_audit")

# Threshold knobs — keep in module so test fixture overrides work cleanly.
_WRITER_STALE_MINUTES = 60         # 4× the 15-min repo_context_writer cadence
_TIER3_VOLUME_THRESHOLD = 3        # firings per 24h before alert
_MIGRATIONS_DIR = Path(__file__).parent.parent / "supabase" / "migrations"
_REPO_ROOT = Path(__file__).parent.parent

# CAI-PROCESS-MAX-FIRST-001 Audit 5 — accepted carve-out reason tokens.
# A direct-API call site is allowed if EITHER (a) its model is Haiku
# (auto-pass via Carve-Out 4) OR (b) it carries a `# llm_route_exempt: <token>`
# comment within ~10 lines and <token> is in this allowlist.
_LLM_ROUTE_EXEMPT_REASONS = frozenset({
    "latency_budget_under_3s",         # Carve-Out 1
    "streaming_structured_output",     # Carve-Out 2
    "vision_multimodal",               # Carve-Out 3
    # (Carve-Out 4 is the Haiku auto-pass — model rule, no comment needed)
    "tool_use_with_caller_defined_tools",  # Carve-Out 5
    # CAI-RESP-174 Q3: ralph Gate 2 PRIMARY remains direct-API until ralph
    # resume_gates validate shadow A/B parity. Ralph is paused since
    # 2026-04-28 so no live cadence at risk. SHADOW path
    # (_shadow_call_ai_gate2) logs to logs/ralph_gate2_shadow.jsonl.
    # Default-flip becomes a ralph resume_gate condition.
    "shadow_ab_primary_pending_resume_gate",
})


async def run_orch_audit(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Run all three audit checks. Fail-isolated: each check's exception is
    logged + tracked but does not block subsequent checks."""
    for check, name in (
        (_audit_writer_health, "writer_health"),
        (_audit_bridge_tier3_volume, "bridge_tier3_volume"),
        (_audit_migration_consistency, "migration_consistency"),
        (_audit_scheduled_sweep_drift, "scheduled_sweep_drift"),
        (_audit_anthropic_sdk_direct_call_sites, "llm_routing_drift"),
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

    msg = format_alert(
        title="Repo state-tracker has stopped updating",
        what=(
            f"The orchestrator's repo state cache hasn't been refreshed for "
            f"{stale_minutes or 'unknown'} minutes (it normally updates every 15)."
        ),
        why=(
            "If the writer is silently failing, downstream features that read "
            "boot_briefing or repo_context (e.g. bug pipeline, deploy verifier) "
            "are working off stale data."
        ),
        do=(
            "Check orch.log for recent 'repo_context_writer' lines — they'll "
            "show whether the writer is crashing or just missing rows."
        ),
        detail=f"Threshold {_WRITER_STALE_MINUTES} min; most-recent updated_at: {most_recent}",
        ref="CAI-RESP-093",
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

    msg = format_alert(
        title="CAI decisions getting mis-routed",
        what=(
            f"{count} of CAI's recent decisions in the last 24h were delivered "
            f"to cc-ihsanos as a default because they didn't say who they were for."
        ),
        why=(
            "Some of those probably needed cc-orchestrator or cc-scholar instead — "
            "they may not have seen rulings that affect them."
        ),
        do=(
            "Nudge CAI to populate announce_to_agent on her strategic_decisions "
            "filings going forward."
        ),
        detail=f"Threshold {_TIER3_VOLUME_THRESHOLD}/24h; query notification_log WHERE source='bridge_tier3_misroute'",
        ref="CAI-PROCESS-ROUTING-001",
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

    file_list = (
        "\n".join(f"  - {n}" for n in missing[:10])
        + (f"\n  (+{len(missing) - 10} more)" if len(missing) > 10 else "")
    )
    msg = format_alert(
        title="Migration files merged but not applied",
        what=(
            f"{len(missing)} .sql migration file(s) exist in source-control "
            f"but were never applied to the live Supabase database."
        ),
        why=(
            "The schema in main is ahead of what's actually running. "
            "Code that depends on those columns/tables will hit "
            "'column does not exist' errors at runtime."
        ),
        do=(
            "Either apply the migrations via psycopg (and record in "
            "schema_migrations) or delete the .sql files if they were "
            "intentionally orphaned."
        ),
        detail=f"Missing files:\n{file_list}",
        ref="ORCH-SELF-AUDIT",
    )
    await _send_and_log(
        supabase,
        bot=bot, musa_chat_id=musa_chat_id, msg=msg,
        source="orch_self_audit.migration_drift",
        decision_ref="ORCH-SELF-AUDIT", dedup_key=dedup_key,
    )


# ----------------------------------------------------------------------------
# Audit 4 — scheduled_sweep_drift: Section D guardrail violation detection
# ----------------------------------------------------------------------------

# Per CAI-RESP-108 axis (d): scheduled-sweep MUST NOT set responded_at.
# This audit catches violations by flagging agent_messages rows where
# responded_at landed within 30s of read_at (suggests the sweep wrote both
# in the same transaction). Watch-note: tune to 60s if false-positive rate
# >5% in first 2 weeks (legitimate fast in-session replies catching the
# alarm). Filter applies only to messages created after CADENCE-001 filing.
_SWEEP_DRIFT_WINDOW_SECONDS = 30

async def _audit_scheduled_sweep_drift(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Alert when a row's responded_at lands within _SWEEP_DRIFT_WINDOW_SECONDS
    of read_at AND the recipient is a CC family (scheduled-sweep population).

    Section D violations come from sweep CCs setting responded_at on inbound
    messages — only CC families run scheduled sweeps. Messages addressed to
    cai are interactive dialogue turns (cai legitimately closes both
    timestamps in one transaction); excluded from the audit.

    Filing-date cutoff so pre-CADENCE-001 rows don't trigger. Hour-bucket
    dedup per message_id."""
    from nervous_system.agent_watchdog import CADENCE_001_FILING_DATE

    now = datetime.now(timezone.utc)
    # Pull recent rows where both read_at + responded_at are set; let the
    # window check happen in Python (PostgREST .lt on a computed delta isn't
    # directly expressible).
    look_back = (now - timedelta(hours=24)).isoformat()
    result = await supabase.table("agent_messages").select(
        "id, from_agent, to_agent, subject, read_at, responded_at, created_at"
    ).gte("created_at", CADENCE_001_FILING_DATE).gte(
        "responded_at", look_back
    ).like("to_agent", "cc-%").not_.is_(
        "read_at", "null"
    ).not_.is_(
        "responded_at", "null"
    ).execute()

    rows = result.data or []
    offenders = []
    for r in rows:
        try:
            read_dt = datetime.fromisoformat(r["read_at"].replace("Z", "+00:00"))
            resp_dt = datetime.fromisoformat(r["responded_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError, KeyError):
            continue
        delta = abs((resp_dt - read_dt).total_seconds())
        if delta < _SWEEP_DRIFT_WINDOW_SECONDS:
            offenders.append((r, delta))

    if not offenders:
        return

    for r, delta in offenders[:5]:  # cap loop blast radius
        dedup_key = f"orch_self_audit:sweep_drift:{r['id']}:{_dedup_bucket(now)}"
        if await _check_dedup(supabase, dedup_key):
            continue
        msg = format_alert(
            title="A scheduled CC sweep may be misbehaving",
            what=(
                f"Message #{r['id']} ({r.get('from_agent')} → {r.get('to_agent')}) "
                f"got marked read AND responded within {int(delta)}s — that's a "
                f"signature of a scheduled-sweep CC writing both fields when it "
                f"should only mark read."
            ),
            why=(
                "Scheduled sweeps aren't supposed to write substantive responses "
                "(Section D). If one is, downstream agents may see fake replies "
                "they didn't actually get."
            ),
            do=(
                f"Check {r.get('to_agent')}'s recent session digests + their "
                f"scheduled-sweep prompt. Tighten the prompt if it's bypassing "
                f"the guardrail."
            ),
            detail=f"Threshold {_SWEEP_DRIFT_WINDOW_SECONDS}s; subject: {(r.get('subject') or '')[:60]}",
            ref="CAI-PROCESS-INBOX-CADENCE-001 Section D",
        )
        await _send_and_log(
            supabase,
            bot=bot, musa_chat_id=musa_chat_id, msg=msg,
            source="orch_self_audit.sweep_drift",
            decision_ref="CAI-RESP-108", dedup_key=dedup_key,
        )


# ----------------------------------------------------------------------------
# Audit 5 — llm_routing_drift: CAI-PROCESS-MAX-FIRST-001 enforcement
# ----------------------------------------------------------------------------

import re

# Match `anthropic.Anthropic(...)` or `anthropic.AsyncAnthropic(...)` instantiations.
# This is the substrate boundary — anything past this line bills against the
# Anthropic API key (auto-recharge credits) instead of the Max plan.
_ANTHROPIC_INSTANTIATION_RE = re.compile(
    r"\banthropic\.(Async)?Anthropic\s*\("
)
# Match `model="..."` or `model='...'` keyword argument value within a few lines.
_MODEL_KW_RE = re.compile(r"""model\s*=\s*['"]([^'"]+)['"]""")
# Match exempt comment: `# llm_route_exempt: <token>`
_EXEMPT_COMMENT_RE = re.compile(r"#\s*llm_route_exempt:\s*([a-z_0-9]+)")

# Files / directories the audit walks. Excludes .venv, __pycache__, tests/,
# node_modules. Tests deliberately excluded — they may import anthropic for
# mocking purposes without making real calls.
_AUDIT_PATHS = (
    _REPO_ROOT,  # walks recursively, filtered below
)
_AUDIT_EXCLUDE_DIRS = frozenset({
    ".venv", "__pycache__", "node_modules", ".git", "tests", "logs",
})


def _scan_call_sites() -> list[dict]:
    """Walk the repo, return one dict per anthropic SDK instantiation.

    Each dict carries: file (str), line (int), model (str|None — file-level
    model detection: first model= literal anywhere in the file, since
    instantiation + .messages.create may be in different functions),
    exempt_reason (str|None — first llm_route_exempt match anywhere in the
    file or within ±10 lines of the instantiation).

    Excludes comment-only lines (starts with `#`) so the regex docstring in
    this module doesn't self-match.
    """
    findings: list[dict] = []
    for root in _AUDIT_PATHS:
        for path in root.rglob("*.py"):
            # Skip excluded dir prefixes
            if any(part in _AUDIT_EXCLUDE_DIRS for part in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            # File-level model detection — first model= literal anywhere.
            # Files using anthropic SDK in production are single-model in
            # practice; this avoids the false negative where instantiation
            # is in helper() and messages.create is 50+ lines away.
            file_model: str | None = None
            for line in lines:
                m = _MODEL_KW_RE.search(line)
                if m:
                    file_model = m.group(1)
                    break

            # File-level exempt detection
            file_exempt: str | None = None
            for line in lines:
                m = _EXEMPT_COMMENT_RE.search(line)
                if m:
                    file_exempt = m.group(1)
                    break

            # Track triple-quoted-string state so docstrings mentioning the
            # trigger pattern don't produce false positives. This is a
            # line-granular state machine — sufficient for routine docstring
            # mentions; a docstring containing the exact regex inside a
            # single line would still false-positive but no such case exists
            # in this repo.
            in_triple_string: str | None = None  # holds the open delimiter or None

            for i, line in enumerate(lines):
                # First handle triple-quoted state — toggle on opening/closing
                # quotes. We scan the line for delimiter pairs.
                remaining = line
                if in_triple_string is not None:
                    end_idx = remaining.find(in_triple_string)
                    if end_idx < 0:
                        # Whole line is inside a docstring — skip
                        continue
                    # Closing found: consume past it, fall through to check
                    # any code AFTER the close
                    remaining = remaining[end_idx + 3:]
                    in_triple_string = None
                # Now look for an OPEN that isn't matched on the same line
                # (i.e., docstring spanning to next line).
                for delim in ('"""', "'''"):
                    open_idx = remaining.find(delim)
                    if open_idx < 0:
                        continue
                    close_idx = remaining.find(delim, open_idx + 3)
                    if close_idx < 0:
                        # Unmatched — docstring continues to next line
                        in_triple_string = delim
                        remaining = remaining[:open_idx]
                        break

                # Skip comment-only lines (handles regex-spec self-match in
                # orch_self_audit.py itself)
                stripped = remaining.lstrip()
                if stripped.startswith("#"):
                    continue
                if not _ANTHROPIC_INSTANTIATION_RE.search(remaining):
                    continue
                findings.append({
                    "file": str(path.relative_to(_REPO_ROOT)),
                    "line": i + 1,
                    "model": file_model,
                    "exempt_reason": file_exempt,
                })
    return findings


def _classify_finding(finding: dict) -> str:
    """Return 'ok_haiku' / 'ok_exempt' / 'violation_no_exempt' / 'violation_invalid_exempt'."""
    model = (finding.get("model") or "").lower()
    if "haiku" in model:
        return "ok_haiku"  # Carve-Out 4 — auto-pass via model rule
    reason = finding.get("exempt_reason")
    if reason is None:
        return "violation_no_exempt"
    if reason in _LLM_ROUTE_EXEMPT_REASONS:
        return "ok_exempt"
    return "violation_invalid_exempt"


async def _audit_anthropic_sdk_direct_call_sites(
    supabase, bot=None, musa_chat_id: str | None = None
) -> None:
    """Alert on direct anthropic SDK instantiations not covered by Carve-Outs
    1–5 of CAI-PROCESS-MAX-FIRST-001.

    Walks the repo, classifies each site, and surfaces violations:
      - violation_no_exempt: Sonnet/Opus call site without `# llm_route_exempt`
      - violation_invalid_exempt: comment present but reason not in allowlist

    Catches drift while the migration sequence is in flight (cto_bot →
    bug_pipeline → council_relay → council_summary). New direct-API call
    sites added during the migration without proper carve-out comments
    fire the audit on the next 10-min tick.

    Hour-bucket dedup per (file, line) so a violation alerts once per hour
    until fixed; doesn't spam.
    """
    try:
        findings = _scan_call_sites()
    except Exception as e:
        logger.error(f"orch_self_audit.llm_routing scan failed: {e}")
        error_tracker.track_exception("orch_self_audit.llm_routing_scan", e)
        return

    violations = [f for f in findings if _classify_finding(f).startswith("violation")]
    if not violations:
        return

    now = datetime.now(timezone.utc)
    for v in violations[:5]:  # cap blast radius
        cls = _classify_finding(v)
        dedup_key = f"orch_self_audit:llm_routing:{v['file']}:{v['line']}:{_dedup_bucket(now)}"
        if await _check_dedup(supabase, dedup_key):
            continue
        msg = format_alert(
            title="New code is burning Anthropic API credits",
            what=(
                f"Found a direct Anthropic API call at {v['file']}:{v['line']} "
                f"that doesn't qualify for any of the 5 cost carve-outs."
            ),
            why=(
                "This call routes to your API key (auto-recharge credits) "
                "instead of the Max plan that's already paid for. Every call "
                "costs real money."
            ),
            do=(
                "Either migrate the caller to ai_provider.call_ai (CLI-route, "
                "Max-covered) OR if it genuinely needs a carve-out, annotate "
                "with `# llm_route_exempt: <reason>` near the call site."
            ),
            detail=(
                f"Model: {v.get('model') or '(not detected)'}; "
                f"Classification: {cls}; "
                f"Exempt: {v.get('exempt_reason') or '(none)'}; "
                f"Valid reasons: {sorted(_LLM_ROUTE_EXEMPT_REASONS)}"
            ),
            ref="CAI-PROCESS-MAX-FIRST-001",
        )
        await _send_and_log(
            supabase,
            bot=bot, musa_chat_id=musa_chat_id, msg=msg,
            source="orch_self_audit.llm_routing_drift",
            decision_ref="CAI-PROCESS-MAX-FIRST-001", dedup_key=dedup_key,
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
