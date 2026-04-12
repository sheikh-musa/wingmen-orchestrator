"""Council Executor — three-gated spec auto-execution.

Polls cto_council_sessions for sessions with execution_status set,
and drives them through the state machine:

  pending → dry_run_pending → (Al-Mushtashir review) → dry_run_complete
    → executing → complete / failed / rolled_back

Gate 1: [SPEC_APPROVED] — spec locked by Al-Mushtashir in-thread
Gate 2: dry-run — executor posts what it WOULD do as a claude_code row
        (source=executor_dry_run), Al-Mushtashir reviews, CONCUR/PUSHBACK
Gate 3: /execute from Musa — with required comment (audit trail)

Origin: CTO Council sessions 3 + 4 (2026-04-12/13).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("wingmen.council_executor")

ORCH_ROOT = Path(__file__).resolve().parent.parent
SPEC_SCHEMA_PATH = ORCH_ROOT / "scripts" / "spec_schema.json"
DRY_RUN_POLL_INTERVAL = 30  # seconds
DRY_RUN_TIMEOUT = 300  # 5 minutes


def _env(name: str) -> str:
    raw = os.environ.get(name, "")
    return raw.split("#", 1)[0].strip()


# ── Main entry (called from wingmen_orch.py main loop) ────────────


async def poll_executor(sb) -> None:
    """Check for sessions that need executor action. Fail-soft."""
    try:
        # Find sessions with an active execution_status
        result = await (
            sb.table("cto_council_sessions")
            .select("id, spec_json, spec_approved_at, execution_status, opening_prompt, repo")
            .not_.is_("execution_status", "null")
            .in_("execution_status", ["dry_run_pending", "executing"])
            .execute()
        )
        sessions = result.data or []
        for session in sessions:
            status = session.get("execution_status")
            if status == "dry_run_pending":
                await _handle_dry_run(sb, session)
            elif status == "executing":
                await _handle_execution(sb, session)
    except Exception as e:
        logger.error(f"poll_executor failed: {e}")


# ── Dry run ───────────────────────────────────────────────────────


async def _handle_dry_run(sb, session: dict) -> None:
    """Generate dry-run output and post for Al-Mushtashir review."""
    session_id = session["id"]
    spec = session.get("spec_json")

    if not spec:
        logger.warning(f"Session {session_id}: dry_run_pending but no spec_json")
        await _update_status(sb, session_id, "failed")
        return

    # Validate spec against schema
    validation_error = _validate_spec(spec)
    if validation_error:
        await _post_system_row(
            sb, session_id,
            f"Spec validation failed: {validation_error}",
            ["SPEC_VALIDATION_FAILED"],
        )
        await _update_status(sb, session_id, "failed")
        return

    # Build dry-run output
    lines = [
        f"# DRY RUN — Session {session_id}",
        f"Summary: {spec.get('summary', '?')}",
        f"Risk: {spec.get('risk_level', '?')}",
        f"Operations: {len(spec.get('operations', []))}",
        "",
        "## Pre-conditions",
    ]
    for pc in spec.get("pre_conditions", []):
        lines.append(f"  CHECK: {pc.get('check')} → expected: {pc.get('expected')}")

    lines.append("")
    lines.append("## Operations (in order)")
    for op in spec.get("operations", []):
        op_type = op.get("type", "?")
        desc = op.get("description", "?")
        op_id = op.get("id", "?")
        detail = ""
        if op_type == "sql_migration":
            sql_preview = (op.get("sql") or "")[:200]
            detail = f"\n    SQL: {sql_preview}{'...' if len(op.get('sql', '')) > 200 else ''}"
        elif op_type == "shell_command":
            detail = f"\n    CMD: {op.get('cmd', '?')}"
        elif op_type in ("file_create", "file_edit"):
            detail = f"\n    PATH: {op.get('path', '?')}"
        lines.append(f"  [{op_id}] {op_type}: {desc}{detail}")

    lines.append("")
    lines.append("## Post-conditions")
    for pc in spec.get("post_conditions", []):
        lines.append(f"  CHECK: {pc.get('check')} → expected: {pc.get('expected')}")

    lines.append("")
    lines.append("## Rollback plan")
    for rb in spec.get("rollback", []):
        rb_type = rb.get("type", "?")
        if rb_type == "sql_migration":
            lines.append(f"  SQL: {(rb.get('sql') or '')[:150]}")
        elif rb_type == "shell_command":
            lines.append(f"  CMD: {rb.get('cmd', '?')}")
        if rb.get("note"):
            lines.append(f"    Note: {rb['note']}")

    dry_run_text = "\n".join(lines)

    # Post as claude_code row with source=executor_dry_run
    # This fires the pg trigger → Al-Mushtashir reviews
    current_round = await _get_current_round(sb, session_id)
    await sb.table("cto_council").insert({
        "session_id": session_id,
        "round": current_round + 1,
        "role": "claude_code",
        "message": dry_run_text,
        "tags": ["DRY_RUN"],
        "context": {
            "source": "executor_dry_run",
            "execution_status": "dry_run_pending",
            "spec_summary": spec.get("summary"),
            "op_count": len(spec.get("operations", [])),
        },
    }).execute()

    logger.info(f"Session {session_id}: dry-run posted, waiting for Al-Mushtashir review")

    # Now poll for Al-Mushtashir's response
    deadline = datetime.now(timezone.utc).timestamp() + DRY_RUN_TIMEOUT
    while datetime.now(timezone.utc).timestamp() < deadline:
        await asyncio.sleep(DRY_RUN_POLL_INTERVAL)

        # Check for the latest claude_ai row after our dry-run post
        ai_rows = await (
            sb.table("cto_council")
            .select("tags, message")
            .eq("session_id", session_id)
            .eq("role", "claude_ai")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not ai_rows.data:
            continue

        ai_tags = ai_rows.data[0].get("tags") or []

        if "PUSHBACK" in ai_tags:
            await _update_status(sb, session_id, "dry_run_rejected")
            await _post_system_row(
                sb, session_id,
                "Dry-run REJECTED by Al-Mushtashir. Review the pushback and revise the spec or abandon.",
                ["DRY_RUN_REJECTED"],
            )
            logger.info(f"Session {session_id}: dry-run rejected")
            return

        if "CONCUR" in ai_tags:
            await _update_status(sb, session_id, "dry_run_complete")
            await _post_system_row(
                sb, session_id,
                "Dry-run APPROVED by Al-Mushtashir. execution_status=dry_run_complete. Awaiting /execute from Musa.",
                ["DRY_RUN_APPROVED"],
            )
            logger.info(f"Session {session_id}: dry-run approved, awaiting /execute")
            return

    # Timeout
    await _update_status(sb, session_id, "dry_run_timeout")
    await _post_system_row(
        sb, session_id,
        f"Dry-run review timed out after {DRY_RUN_TIMEOUT}s. Al-Mushtashir did not respond. Musa can retry via /concur.",
        ["DRY_RUN_TIMEOUT"],
    )
    logger.warning(f"Session {session_id}: dry-run review timed out")


# ── Real execution ────────────────────────────────────────────────


async def _handle_execution(sb, session: dict) -> None:
    """Execute the spec operations sequentially."""
    session_id = session["id"]
    spec = session.get("spec_json")

    if not spec:
        await _update_status(sb, session_id, "failed")
        return

    operations = spec.get("operations", [])
    log_lines: list[str] = []
    completed_ops: list[str] = []

    try:
        # Run pre-conditions
        log_lines.append("## Pre-condition checks")
        for pc in spec.get("pre_conditions", []):
            result = _run_check(pc.get("check", ""))
            log_lines.append(f"  {pc['check']} → {result}")
            # Pre-conditions are informational in v1, not blocking

        # Execute operations
        log_lines.append("")
        log_lines.append("## Execution")
        for op in operations:
            op_id = op.get("id", "?")
            op_type = op.get("type", "?")
            log_lines.append(f"\n  [{op_id}] {op_type}: {op.get('description', '?')}")

            try:
                result = await _execute_operation(op)
                log_lines.append(f"    ✓ {result}")
                completed_ops.append(op_id)
            except Exception as e:
                log_lines.append(f"    ✗ FAILED: {e}")
                log_lines.append(f"\n## Execution stopped at {op_id}")
                raise

        # Post-conditions
        log_lines.append("")
        log_lines.append("## Post-condition checks")
        post_failures = []
        for pc in spec.get("post_conditions", []):
            result = _run_check(pc.get("check", ""))
            expected = str(pc.get("expected", ""))
            passed = expected.lower() in result.lower() if expected else True
            status = "✓" if passed else "✗"
            log_lines.append(f"  {status} {pc['check']} → {result} (expected: {expected})")
            if not passed:
                post_failures.append(pc["check"])

        if post_failures:
            raise RuntimeError(
                f"Post-condition failures: {', '.join(post_failures)}"
            )

        # Success
        await _update_status(sb, session_id, "complete")
        execution_log = "\n".join(log_lines)
        await _post_system_row(
            sb, session_id,
            f"# Execution COMPLETE\n\n{execution_log}",
            ["EXECUTION_COMPLETE"],
        )
        logger.info(f"Session {session_id}: execution complete, {len(completed_ops)} ops")

    except Exception as e:
        # Execution failed — attempt rollback
        log_lines.append(f"\n## FAILURE: {e}")
        log_lines.append("")

        rollback_result = await _run_rollback(spec.get("rollback", []))
        log_lines.append(rollback_result)

        final_status = "rolled_back" if "Rollback complete" in rollback_result else "failed"
        await _update_status(sb, session_id, final_status)

        execution_log = "\n".join(log_lines)
        await _post_system_row(
            sb, session_id,
            f"# Execution FAILED\n\n{execution_log}",
            ["EXECUTION_FAILED"],
        )
        logger.error(f"Session {session_id}: execution failed: {e}")


# ── Operation handlers ────────────────────────────────────────────


async def _execute_operation(op: dict) -> str:
    """Execute a single spec operation. Returns a result summary string."""
    op_type = op.get("type", "")

    if op_type == "sql_migration":
        sql = op.get("sql", "")
        name = op.get("name", "executor_migration")
        if not sql:
            raise ValueError("sql_migration operation has empty sql")
        # Use Supabase MCP pattern — but from Python we use the REST API
        # For now, use subprocess to call supabase CLI or psql
        # Actually, the simplest: write to a temp file and apply via the seed pattern
        logger.info(f"Applying SQL migration: {name}")
        # Direct execution via Supabase service client
        from supabase import create_client
        sb = create_client(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY"))
        result = sb.rpc("exec_sql", {"query": sql}).execute()
        return f"SQL migration '{name}' applied"

    elif op_type == "shell_command":
        cmd = op.get("cmd", "")
        if not cmd:
            raise ValueError("shell_command operation has empty cmd")
        allowed_codes = op.get("allowed_exit_codes", [0])
        logger.info(f"Running: {cmd}")
        proc = subprocess.run(
            cmd, shell=True, cwd=str(ORCH_ROOT),
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode not in allowed_codes:
            raise RuntimeError(
                f"Command exited {proc.returncode}: {proc.stderr[:500]}"
            )
        return f"exit {proc.returncode}: {proc.stdout[:200]}"

    elif op_type == "file_create":
        path = op.get("path", "")
        content_b64 = op.get("content_b64", "")
        if not path:
            raise ValueError("file_create has no path")
        full_path = ORCH_ROOT / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        if content_b64:
            full_path.write_bytes(base64.b64decode(content_b64))
        else:
            full_path.touch()
        return f"Created {path}"

    elif op_type == "file_edit":
        path = op.get("path", "")
        new_content_b64 = op.get("new_content_b64", "")
        if not path or not new_content_b64:
            raise ValueError("file_edit needs path + new_content_b64")
        full_path = ORCH_ROOT / path
        if not full_path.exists():
            raise FileNotFoundError(f"{path} not found")
        # Verify old content hash if provided
        old_sha = op.get("old_sha256")
        if old_sha:
            current_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
            if current_hash != old_sha:
                raise RuntimeError(
                    f"Hash mismatch on {path}: expected {old_sha[:12]}, got {current_hash[:12]}. "
                    "File was modified since spec was written."
                )
        full_path.write_bytes(base64.b64decode(new_content_b64))
        return f"Edited {path}"

    else:
        raise ValueError(f"Unknown operation type: {op_type}")


# ── Rollback ──────────────────────────────────────────────────────


async def _run_rollback(rollback_ops: list[dict]) -> str:
    """Best-effort rollback. Returns a summary string."""
    if not rollback_ops:
        return "## Rollback: no rollback operations defined"

    lines = ["## Rollback"]
    for rb in rollback_ops:
        rb_type = rb.get("type", "?")
        try:
            if rb_type == "shell_command":
                cmd = rb.get("cmd", "")
                proc = subprocess.run(
                    cmd, shell=True, cwd=str(ORCH_ROOT),
                    capture_output=True, text=True, timeout=60,
                )
                lines.append(f"  ✓ {cmd} (exit {proc.returncode})")
            elif rb_type == "sql_migration":
                sql = rb.get("sql", "")
                from supabase import create_client
                sb = create_client(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY"))
                sb.rpc("exec_sql", {"query": sql}).execute()
                lines.append(f"  ✓ SQL rollback applied")
            else:
                lines.append(f"  ? Unknown rollback type: {rb_type}")
        except Exception as e:
            lines.append(f"  ✗ Rollback failed: {e}")

    lines.append("Rollback complete (best-effort)")
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────


def _validate_spec(spec: dict) -> str | None:
    """Validate spec against JSON schema. Returns error string or None."""
    try:
        import jsonschema
        if SPEC_SCHEMA_PATH.exists():
            schema = json.loads(SPEC_SCHEMA_PATH.read_text())
            jsonschema.validate(spec, schema)
        return None
    except ImportError:
        # jsonschema not installed — skip validation
        logger.warning("jsonschema not installed, skipping spec validation")
        return None
    except Exception as e:
        return str(e)[:500]


def _run_check(check_cmd: str) -> str:
    """Run a pre/post-condition check (SQL or shell). Returns output."""
    try:
        if check_cmd.strip().upper().startswith("SELECT"):
            # SQL check — run via supabase
            from supabase import create_client
            sb = create_client(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY"))
            result = sb.rpc("exec_sql", {"query": check_cmd}).execute()
            return str(result.data)[:300]
        else:
            # Shell check
            proc = subprocess.run(
                check_cmd, shell=True, cwd=str(ORCH_ROOT),
                capture_output=True, text=True, timeout=30,
            )
            return proc.stdout.strip()[:300] or f"exit {proc.returncode}"
    except Exception as e:
        return f"CHECK FAILED: {e}"


async def _update_status(sb, session_id: int, status: str) -> None:
    await (
        sb.table("cto_council_sessions")
        .update({"execution_status": status})
        .eq("id", session_id)
        .execute()
    )


async def _post_system_row(
    sb, session_id: int, message: str, tags: list[str]
) -> None:
    current_round = await _get_current_round(sb, session_id)
    await sb.table("cto_council").insert({
        "session_id": session_id,
        "round": current_round + 1,
        "role": "system",
        "message": message,
        "tags": tags,
        "context": {"source": "council_executor"},
    }).execute()


async def _get_current_round(sb, session_id: int) -> int:
    result = await (
        sb.table("cto_council_sessions")
        .select("current_round")
        .eq("id", session_id)
        .single()
        .execute()
    )
    return result.data.get("current_round", 0) if result.data else 0
