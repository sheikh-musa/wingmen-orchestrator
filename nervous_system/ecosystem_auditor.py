"""Ecosystem Auditor — seven self-maintaining governance gates.

ARCH-023: Prevents manual state reconciliation by running automated checks
and (optionally) auto-healing governance tables on a schedule.

Gates and cadences:
  GATE 1 — Challenge window auto-flip       every hour
  GATE 2 — Ship verification auto-implement every 30 min
  GATE 3 — Stale blocker escalation         daily at 06:00 SGT
  GATE 4 — CC-UPDATE auto-classification    every 5 min
  GATE 5 — Archive                          daily at 03:00 SGT (archive.py owns this)
  GATE 6 — Contradiction detection          every 6 hours (Haiku API)
  GATE 7 — Orphan detection                 daily at 04:00 SGT

Safety: ECOSYSTEM_DRY_RUN=true (default) — audit log only, no mutations.
Flip each gate's DRY_RUN to live per-gate as outputs are validated.

All gate runs are logged to ecosystem_audit_log with rows_affected and
actions_taken JSON. The team dashboard reads this table for the
"Ecosystem Health" panel.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("wingmen.ecosystem")

# ── Gate cadence state (in-process) ──────────────────────────────────────────
_last_g1_run: datetime | None = None   # hourly
_last_g2_run: datetime | None = None   # every 30 min
_last_g3_date: str | None = None       # daily at 06:00 SGT
_last_g6_run: datetime | None = None   # every 6 hours
_last_g7_date: str | None = None       # daily at 04:00 SGT

_SGT = timezone(timedelta(hours=8))

# ── DRY_RUN per gate ─────────────────────────────────────────────────────────
# Override env vars: ECOSYSTEM_DRY_RUN=false disables all gates.
# Per-gate: ECOSYSTEM_G1_DRY_RUN, G2, G3, G4, G6, G7 (G5 is archive.py).

def _dry_run(gate: str) -> bool:
    """Return True if this gate should log-only (no mutations)."""
    per_gate = os.environ.get(f"ECOSYSTEM_{gate}_DRY_RUN")
    if per_gate is not None:
        return per_gate.lower() not in ("false", "0", "no")
    global_dry = os.environ.get("ECOSYSTEM_DRY_RUN", "true")
    return global_dry.lower() not in ("false", "0", "no")


async def _log_gate_run(
    supabase,
    gate_name: str,
    dry_run: bool,
    rows_affected: int,
    actions: list[dict],
    notes: str | None = None,
) -> None:
    """Write a row to ecosystem_audit_log."""
    try:
        supabase.table("ecosystem_audit_log").insert({
            "gate_name": gate_name,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "rows_affected": rows_affected,
            "actions_taken": actions,
            "notes": notes,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to write audit log for {gate_name}: {e}")


# ── GATE 1: Challenge Window Auto-Flip ───────────────────────────────────────

async def run_gate1_challenge_flip(supabase) -> None:
    """Flip expired challenge_window decisions to accepted (silence = consent)."""
    global _last_g1_run
    now = datetime.now(timezone.utc)

    if _last_g1_run and (now - _last_g1_run).total_seconds() < 3600:
        return

    _last_g1_run = now
    dry = _dry_run("G1")
    logger.debug(f"GATE 1 running (dry_run={dry})")

    # Find expired challenge windows from CC or orchestrator proposals
    expired = (
        supabase.table("strategic_decisions")
        .select("id,decision_ref,challenge_status,source,challengeable_until")
        .eq("challenge_status", "challenge_window")
        .lt("challengeable_until", now.isoformat())
        .in_("source", ["claude_ai_session", "claude_code_proposal", "claude_code"])
        .execute()
        .data or []
    )

    actions: list[dict] = []
    for row in expired:
        ref = row["decision_ref"]
        action = {
            "ref": ref,
            "old_status": "challenge_window",
            "new_status": "accepted",
            "reason": "challenge_window_expired",
        }
        if not dry:
            supabase.table("strategic_decisions").update({
                "challenge_status": "accepted"
            }).eq("id", row["id"]).execute()
        actions.append(action)
        logger.info(f"GATE 1: {'[DRY] ' if dry else ''}flip {ref} challenge_window→accepted (expired)")

    # Also: decisions stuck in cai_review_requested for > 48h — escalate but don't flip
    stale_reviews = (
        supabase.table("strategic_decisions")
        .select("id,decision_ref,updated_at")
        .eq("challenge_status", "cai_review_requested")
        .lt("updated_at", (now - timedelta(hours=48)).isoformat())
        .execute()
        .data or []
    )
    for row in stale_reviews:
        logger.warning(f"GATE 1: {row['decision_ref']} has been cai_review_requested for >48h")
        actions.append({
            "ref": row["decision_ref"],
            "action": "stale_review_flagged",
            "reason": "cai_review_requested_48h_no_update",
        })

    await _log_gate_run(supabase, "G1_challenge_flip", dry, len(expired), actions,
                        notes=f"expired={len(expired)}, stale_reviews={len(stale_reviews)}")


# ── GATE 2: Ship Verification Auto-Implement ─────────────────────────────────

async def run_gate2_ship_verify(supabase) -> None:
    """Flip accepted decisions to implemented when cc_work_sessions shows completion."""
    global _last_g2_run
    now = datetime.now(timezone.utc)

    if _last_g2_run and (now - _last_g2_run).total_seconds() < 1800:
        return

    _last_g2_run = now
    dry = _dry_run("G2")
    logger.debug(f"GATE 2 running (dry_run={dry})")

    # Find accepted decisions that have been implemented (referenced in completed sessions)
    accepted = (
        supabase.table("strategic_decisions")
        .select("id,decision_ref,title")
        .eq("challenge_status", "accepted")
        .execute()
        .data or []
    )

    # Get completed cc_work_sessions with commit_sha
    completed_sessions = (
        supabase.table("cc_work_sessions")
        .select("id,repo_name,outcome,commit_sha,job_id")
        .eq("outcome", "completed")
        .not_.is_("commit_sha", "null")
        .order("created_at", desc=True)
        .limit(100)
        .execute()
        .data or []
    )

    # Map job_id -> session for cross-reference
    completed_job_ids = {s["job_id"] for s in completed_sessions if s.get("job_id")}

    # Check if any accepted decision has a completed job linked
    actions: list[dict] = []
    for dec in accepted:
        rows = (
            supabase.table("strategic_decisions")
            .select("id,decision_ref,completed_job_id,execution_status")
            .eq("decision_ref", dec["decision_ref"])
            .not_.is_("completed_job_id", "null")
            .execute()
            .data or []
        )
        for row in rows:
            cjid = row.get("completed_job_id")
            if cjid and cjid in completed_job_ids and row.get("execution_status") != "implemented":
                action = {
                    "ref": row["decision_ref"],
                    "old_status": "accepted",
                    "new_status": "implemented",
                    "completed_job_id": cjid,
                    "reason": "cc_work_session_completed_with_commit",
                }
                if not dry:
                    supabase.table("strategic_decisions").update({
                        "challenge_status": "implemented",
                        "execution_status": "implemented",
                        "completed_at": now.isoformat(),
                    }).eq("id", row["id"]).execute()
                actions.append(action)
                logger.info(f"GATE 2: {'[DRY] ' if dry else ''}auto-implement {row['decision_ref']} (job #{cjid})")

    await _log_gate_run(supabase, "G2_ship_verify", dry, len(actions), actions,
                        notes=f"checked {len(accepted)} accepted decisions, {len(completed_sessions)} completed sessions")


# ── GATE 3: Stale Blocker Escalation ─────────────────────────────────────────

async def run_gate3_stale_blocker(supabase, bot=None, musa_id: str = "") -> None:
    """Flag challenged decisions and stale repo_context blockers to Musa."""
    global _last_g3_date
    now = datetime.now(timezone.utc)
    sgt_now = now.astimezone(_SGT)

    if sgt_now.hour != 6 or sgt_now.strftime("%Y-%m-%d") == _last_g3_date:
        return

    _last_g3_date = sgt_now.strftime("%Y-%m-%d")
    dry = _dry_run("G3")
    logger.debug(f"GATE 3 running (dry_run={dry})")

    cutoff_72h = (now - timedelta(hours=72)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    # Challenged decisions with no update in 72h
    stale_challenged = (
        supabase.table("strategic_decisions")
        .select("decision_ref,title,challenge_reason,updated_at")
        .eq("challenge_status", "challenged")
        .lt("updated_at", cutoff_72h)
        .execute()
        .data or []
    )

    # Stale repo_context blockers
    stale_blockers = (
        supabase.table("repo_context")
        .select("repo,blockers,updated_at")
        .lt("updated_at", cutoff_7d)
        .execute()
        .data or []
    )
    stale_blockers = [r for r in stale_blockers if r.get("blockers")]

    actions: list[dict] = []
    for row in stale_challenged:
        logger.warning(f"GATE 3: {row['decision_ref']} challenged >72h with no update")
        actions.append({"ref": row["decision_ref"], "type": "stale_challenged", "updated_at": row["updated_at"]})
        if bot and musa_id and not dry:
            try:
                msg = (
                    f"⚠️ GATE 3: Decision [{row['decision_ref']}] has been "
                    f"`challenged` for >72h with no resolution.\n"
                    f"Title: {row['title'][:80]}\n"
                    f"Challenge: {(row.get('challenge_reason') or 'none')[:100]}"
                )
                await bot.send_message(chat_id=musa_id, text=msg, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"GATE 3 Telegram failed: {e}")

    for row in stale_blockers:
        logger.warning(f"GATE 3: {row['repo']} has stale blockers >7d")
        actions.append({"repo": row["repo"], "type": "stale_blocker", "updated_at": row["updated_at"]})

    await _log_gate_run(supabase, "G3_stale_blocker", dry, len(actions), actions,
                        notes=f"challenged={len(stale_challenged)}, stale_blockers={len(stale_blockers)}")


# ── GATE 4: CC-UPDATE Auto-Classification ────────────────────────────────────

async def run_gate4_cc_classification(supabase) -> None:
    """Tag CC-UPDATE-* and informational rows as challenge_status=informational."""
    dry = _dry_run("G4")

    # Find status/update rows that are still in challenge_window or unchallenged
    candidates = (
        supabase.table("strategic_decisions")
        .select("id,decision_ref,challenge_status")
        .in_("challenge_status", ["challenge_window", "unchallenged"])
        .execute()
        .data or []
    )

    # Filter to CC-UPDATE-* and similar operational report patterns
    _INFO_PREFIXES = ("CC-UPDATE-", "IMPL-UPDATE-", "CAI-STATUS-", "DIGEST-")
    to_tag = [
        r for r in candidates
        if any(r["decision_ref"].startswith(p) for p in _INFO_PREFIXES)
    ]

    if not to_tag:
        return

    actions: list[dict] = []
    for row in to_tag:
        action = {
            "ref": row["decision_ref"],
            "old_status": row["challenge_status"],
            "new_status": "informational",
            "reason": "cc_update_auto_classification",
        }
        if not dry:
            supabase.table("strategic_decisions").update({
                "challenge_status": "informational"
            }).eq("id", row["id"]).execute()
        actions.append(action)
        logger.info(f"GATE 4: {'[DRY] ' if dry else ''}tag {row['decision_ref']} as informational")

    await _log_gate_run(supabase, "G4_cc_classification", dry, len(to_tag), actions)


# ── GATE 6: Contradiction Detection ──────────────────────────────────────────

async def run_gate6_contradiction(supabase, bot=None, musa_id: str = "") -> None:
    """Scan accepted+implemented decisions for semantic contradictions via Haiku."""
    global _last_g6_run
    now = datetime.now(timezone.utc)

    if _last_g6_run and (now - _last_g6_run).total_seconds() < 21600:
        return

    _last_g6_run = now
    dry = _dry_run("G6")
    logger.debug(f"GATE 6 running (dry_run={dry})")

    # Fetch active accepted + implemented decisions (index only)
    rows = (
        supabase.table("strategic_decisions")
        .select("decision_ref,title,domain,category")
        .in_("challenge_status", ["accepted", "challenge_window"])
        .eq("status", "active")
        .order("decided_at", desc=True)
        .limit(60)
        .execute()
        .data or []
    )

    if len(rows) < 4:
        logger.debug("GATE 6: not enough decisions to check for contradictions")
        return

    decision_index = "\n".join(
        f"  {r['decision_ref']} [{r.get('domain','?')}/{r.get('category','?')}]: {r['title'][:80]}"
        for r in rows
    )

    prompt = (
        "You are analysing a governance decision index for contradictions. "
        "Identify any decisions that directly conflict with each other. "
        "Return JSON only: {\"contradictions\": [{\"refs\": [\"A\", \"B\"], \"description\": \"...\"}]}. "
        "If none, return {\"contradictions\": []}. Be conservative — only flag clear conflicts.\n\n"
        f"Decisions:\n{decision_index}"
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        result = json.loads(raw)
        contradictions = result.get("contradictions", [])
    except Exception as e:
        logger.error(f"GATE 6 Haiku call failed: {e}")
        return

    actions: list[dict] = []
    for c in contradictions:
        refs = c.get("refs", [])
        desc = c.get("description", "")
        logger.warning(f"GATE 6: contradiction detected between {refs}: {desc}")
        actions.append({"type": "contradiction", "refs": refs, "description": desc})

        if bot and musa_id and not dry:
            try:
                await bot.send_message(
                    chat_id=musa_id,
                    text=f"⚠️ GATE 6: Contradiction detected\nRefs: {refs}\n{desc[:200]}",
                )
            except Exception as e:
                logger.error(f"GATE 6 Telegram failed: {e}")

    await _log_gate_run(
        supabase, "G6_contradiction", dry, len(contradictions), actions,
        notes=f"checked {len(rows)} decisions, found {len(contradictions)} contradictions"
    )


# ── GATE 7: Orphan Detection ─────────────────────────────────────────────────

async def run_gate7_orphan_detection(supabase, bot=None, musa_id: str = "") -> None:
    """Find orphaned decisions, sessions, and messages."""
    global _last_g7_date
    now = datetime.now(timezone.utc)
    sgt_now = now.astimezone(_SGT)

    if sgt_now.hour != 4 or sgt_now.strftime("%Y-%m-%d") == _last_g7_date:
        return

    _last_g7_date = sgt_now.strftime("%Y-%m-%d")
    dry = _dry_run("G7")
    logger.debug(f"GATE 7 running (dry_run={dry})")

    # Load known repo names from REPOS.json
    try:
        import json as _json
        from pathlib import Path
        repos_data = _json.loads((Path(__file__).parent.parent / "REPOS.json").read_text())
        known_repos = {r["name"] for r in repos_data.get("repos", [])}
    except Exception:
        known_repos = set()

    actions: list[dict] = []

    # Orphaned sessions: cc_work_sessions with no matching strategic_decisions
    if known_repos:
        unknown_repo_sessions = (
            supabase.table("cc_work_sessions")
            .select("id,repo_name,outcome,created_at")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
            .data or []
        )
        unknown_repo_sessions = [
            s for s in unknown_repo_sessions
            if s.get("repo_name") and s["repo_name"] not in known_repos
        ]
        for s in unknown_repo_sessions:
            logger.warning(f"GATE 7: cc_work_session #{s['id']} references unknown repo {s['repo_name']}")
            actions.append({
                "type": "unknown_repo_session",
                "session_id": s["id"],
                "repo_name": s["repo_name"],
            })

    # qa_findings with no status change in 14 days
    cutoff_14d = (now - timedelta(days=14)).isoformat()
    stale_qa = (
        supabase.table("qa_findings")
        .select("id,repo,role,flow,status,found_at")
        .eq("status", "fail")
        .is_("resolved_at", "null")
        .lt("found_at", cutoff_14d)
        .limit(20)
        .execute()
        .data or []
    )
    for f in stale_qa:
        key = f"{f['repo']}/{f['role']}/{f['flow']}"
        logger.warning(f"GATE 7: qa_finding {key} has been failing for >14 days")
        actions.append({
            "type": "stale_qa_failure",
            "key": key,
            "found_at": f.get("found_at"),
        })

    await _log_gate_run(
        supabase, "G7_orphan_detection", dry, len(actions), actions,
        notes=f"stale_qa={len(stale_qa)}, unknown_repo_sessions={len([a for a in actions if a['type']=='unknown_repo_session'])}"
    )


# ── Main entry points (called from wingmen_orch.py) ───────────────────────────

async def run_frequent_gates(supabase) -> None:
    """GATE 4 — every 5 min (called every 10 polls)."""
    try:
        await run_gate4_cc_classification(supabase)
    except Exception as e:
        logger.error(f"GATE 4 failed: {e}")


async def run_half_hour_gates(supabase) -> None:
    """GATE 2 — every 30 min (called every 60 polls)."""
    try:
        await run_gate2_ship_verify(supabase)
    except Exception as e:
        logger.error(f"GATE 2 failed: {e}")


async def run_hourly_gates(supabase) -> None:
    """GATE 1 — hourly (called every 120 polls)."""
    try:
        await run_gate1_challenge_flip(supabase)
    except Exception as e:
        logger.error(f"GATE 1 failed: {e}")


async def run_six_hour_gates(supabase, bot=None, musa_id: str = "") -> None:
    """GATE 6 — every 6 hours (called every 720 polls, but gates inside)."""
    try:
        await run_gate6_contradiction(supabase, bot, musa_id)
    except Exception as e:
        logger.error(f"GATE 6 failed: {e}")


async def run_daily_gates(supabase, bot=None, musa_id: str = "") -> None:
    """GATES 3 + 7 — daily (called every poll, internal time-checks gate them)."""
    try:
        await run_gate3_stale_blocker(supabase, bot, musa_id)
    except Exception as e:
        logger.error(f"GATE 3 failed: {e}")
    try:
        await run_gate7_orphan_detection(supabase, bot, musa_id)
    except Exception as e:
        logger.error(f"GATE 7 failed: {e}")
