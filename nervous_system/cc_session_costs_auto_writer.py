"""cc_session_costs M1 transcript-tail parser per CC-LONG-CALLER-AUTO-TOKEN-TRACK-001-RESUME.

Sweeps ~/.claude/projects/<mangled-repo>/*.jsonl. For each jsonl, parses
'usage' fields from assistant messages, sums them, returns SessionTokens.
sweep_projects_root attributes via _DIR_TO_CC and returns upsert-ready row dicts.

Pure-Python. No I/O at decision-time except the defensive jsonl_safe_read calls.
Caller wires it into orch main loop + commits the upsert batch.

R3 inheritance: all reads are defensive — missing/corrupt files return None.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from nervous_system.autonomous_loop_detector import _DIR_TO_CC
from nervous_system.jsonl_safe_read import safe_file_stats


@dataclass(frozen=True)
class SessionTokens:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


def parse_jsonl_usage(path: Path) -> Optional[SessionTokens]:
    """Stream-parse a Claude CLI jsonl and sum usage across assistant messages.

    Returns None on any error (missing, corrupt, unreadable). Returns
    SessionTokens(0,0,0,0) for a jsonl with no assistant messages.
    """
    try:
        in_t = out_t = cc_t = cr_t = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return None  # corrupt — bail
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                in_t += int(usage.get("input_tokens") or 0)
                out_t += int(usage.get("output_tokens") or 0)
                cc_t += int(usage.get("cache_creation_input_tokens") or 0)
                cr_t += int(usage.get("cache_read_input_tokens") or 0)
        return SessionTokens(
            input_tokens=in_t,
            output_tokens=out_t,
            cache_creation_input_tokens=cc_t,
            cache_read_input_tokens=cr_t,
        )
    except (OSError, FileNotFoundError):
        return None


def sweep_projects_root(
    projects_root: Path,
    modified_since: float,
) -> list[dict[str, Any]]:
    """Walk projects_root/<mangled-repo>/*.jsonl, parse usage, return upsert rows.

    `modified_since`: epoch-seconds cutoff. Skip jsonls older than this. Used by
    the orch wrapper to do incremental sweeps (e.g., last 10min only).

    Each returned row is a dict ready for INSERT INTO cc_session_costs:
        cc_identity, session_id, input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens, mtime
    """
    rows: list[dict[str, Any]] = []
    if not projects_root.exists():
        return rows
    try:
        repo_dirs = [d for d in projects_root.iterdir() if d.is_dir()]
    except OSError:
        return rows
    for repo_dir in repo_dirs:
        cc_identity = _DIR_TO_CC.get(repo_dir.name)
        if not cc_identity:
            continue
        try:
            jsonls = [
                p for p in repo_dir.iterdir()
                if p.is_file() and p.name.endswith(".jsonl") and not p.name.startswith(".")
            ]
        except OSError:
            continue
        for jsonl in jsonls:
            stats = safe_file_stats(jsonl)
            if stats is None:
                continue
            if stats.mtime < modified_since:
                continue
            tokens = parse_jsonl_usage(jsonl)
            if tokens is None:
                continue
            session_id = jsonl.stem  # filename without .jsonl
            rows.append({
                "cc_identity": cc_identity,
                "session_id": session_id,
                "input_tokens": tokens.input_tokens,
                "output_tokens": tokens.output_tokens,
                "cache_creation_input_tokens": tokens.cache_creation_input_tokens,
                "cache_read_input_tokens": tokens.cache_read_input_tokens,
                "mtime": stats.mtime,
            })
    return rows


def upsert_rows(dsn: str, rows: list[dict[str, Any]], source: str = "auto_writer_v1") -> int:
    """Upsert rows into cc_session_costs. Returns number of rows written.

    Conflict resolution: session_id is the natural key but the table doesn't
    currently have a UNIQUE constraint on it. We dedupe by session_id within
    the batch and UPDATE-on-conflict using a manual select-then-insert pattern.
    """
    if not rows:
        return 0
    import psycopg
    written = 0
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                SELECT id FROM cc_session_costs WHERE session_id = %s AND source = %s LIMIT 1
                """,
                (row["session_id"], source),
            )
            existing = cur.fetchone()
            from datetime import datetime, timezone
            started_at = datetime.fromtimestamp(row["mtime"], tz=timezone.utc)
            if existing:
                cur.execute(
                    """
                    UPDATE cc_session_costs SET
                      input_tokens = %s,
                      output_tokens = %s,
                      cache_creation_input_tokens = %s,
                      cache_read_input_tokens = %s,
                      ended_at = %s
                    WHERE id = %s
                    """,
                    (
                        row["input_tokens"],
                        row["output_tokens"],
                        row["cache_creation_input_tokens"],
                        row["cache_read_input_tokens"],
                        started_at,
                        existing[0],
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO cc_session_costs
                      (cc_identity, session_id, started_at, ended_at,
                       input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens,
                       source, has_per_message_detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)
                    """,
                    (
                        row["cc_identity"], row["session_id"], started_at, started_at,
                        row["input_tokens"], row["output_tokens"],
                        row["cache_creation_input_tokens"], row["cache_read_input_tokens"],
                        source,
                    ),
                )
            written += 1
    return written
