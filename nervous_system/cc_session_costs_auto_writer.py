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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from nervous_system.autonomous_loop_detector import _DIR_TO_CC
from nervous_system.jsonl_safe_read import safe_file_stats


# Project-dir names are host-specific: the _DIR_TO_CC keys are the Mini's
# `-Users-sheikhmusa-...` form, but the same repo on another host (e.g. the
# Studio hub, home /Users/Musa -> `-Users-Musa-...`) has a different dir name.
# Rewrite the leading `-Users-<user>-` to the canonical Mini form so the sweep
# resolves cc_identity regardless of which host runs it (the 2026-07-08 topology
# move to the Studio hub is exactly why the writer silently matched zero dirs).
_HOME_PREFIX_RE = re.compile(r"^-Users-[^-]+-")


def _canonical_dir(name: str) -> str:
    return _HOME_PREFIX_RE.sub("-Users-sheikhmusa-", name, count=1)


# LANE COVERAGE (op-caught 2026-07-26: "is everyone's context on fleet console
# accurate?"). _DIR_TO_CC only knows the four original CC families + the
# operator-session repos. Every lane spun up since then — the four live Mini
# lanes and the fleet SRE — has a ~/.claude/projects dir that map has never
# heard of, so `if not cc_identity: continue` DROPPED them silently and they
# were absent from cc_session_costs entirely. The context gauge therefore
# reported on 6 bodies while 5 more ran unmeasured; cc-irsyad was measured at
# 481,207 live tokens (48% of a 1M window) while showing NOWHERE.
#
# Deliberately a SEPARATE map rather than new _DIR_TO_CC entries: _DIR_TO_CC
# also drives autonomous_loop_detector's runaway flagging and watchdog.py's
# long-caller content-shape/kill pipeline. Widening it would silently enrol
# five live lanes into a KILL path. Telemetry coverage must not buy itself a
# blast radius — so this map is read ONLY here, by the cost/context writer.
#
# Keys: the Mini's ~/.claude/projects dir names (host-normalised by
# _canonical_dir, same as _DIR_TO_CC). Values: the lane's canonical bus
# identity (fleet_lanes.base_agent_id — verified against agent_messages
# from_agent, NOT the '-1'-suffixed agent_status instance ids).
_LANE_DIR_TO_CC = {
    "-Users-sheikhmusa-wingmen-projects-ihsanos-irsyad":     "cc-irsyad",
    "-Users-sheikhmusa-wingmen-projects-cosem-exams-lane":   "cc-cosem-exams",
    "-Users-sheikhmusa-wingmen-projects-caai-lane":          "cc-caai",
    "-Users-sheikhmusa-wingmen-projects-cosem-port-lane":    "cc-cosem-platform",
    "-Users-sheikhmusa-wingmen-projects-cosem-platform":     "cc-cosem-platform",
    "-Users-sheikhmusa-wingmen-fleet-health":                "cc-fleet-health",
}


def resolve_cc_identity(dir_name: str) -> Optional[str]:
    """cc_identity for a ~/.claude/projects dir name, or None if unmapped.

    Order: the ratified _DIR_TO_CC families first (so nothing here can shadow
    a canonical identity), then the lane map. Both are tried on the raw name
    and on the host-normalised form.
    """
    for table in (_DIR_TO_CC, _LANE_DIR_TO_CC):
        hit = table.get(dir_name) or table.get(_canonical_dir(dir_name))
        if hit:
            return hit
    return None


@dataclass(frozen=True)
class SessionTokens:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    # Current-context size = the LAST assistant turn's actual input context
    # (fresh input + cache-read + cache-creation). Unlike the summed fields
    # above (lifetime cost), this is how full the window is *right now*.
    latest_context_tokens: int = 0


def parse_jsonl_usage(path: Path) -> Optional[SessionTokens]:
    """Stream-parse a Claude CLI jsonl and sum usage across assistant messages.

    Returns None on any error (missing, corrupt, unreadable). Returns
    SessionTokens(0,0,0,0) for a jsonl with no assistant messages.
    """
    try:
        in_t = out_t = cc_t = cr_t = 0
        last_ctx = 0
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
                m_in = int(usage.get("input_tokens") or 0)
                m_cc = int(usage.get("cache_creation_input_tokens") or 0)
                m_cr = int(usage.get("cache_read_input_tokens") or 0)
                in_t += m_in
                out_t += int(usage.get("output_tokens") or 0)
                cc_t += m_cc
                cr_t += m_cr
                # Overwrite each turn -> holds the LAST assistant turn's context
                # depth (the live window fill), not the running sum.
                last_ctx = m_in + m_cr + m_cc
        return SessionTokens(
            input_tokens=in_t,
            output_tokens=out_t,
            cache_creation_input_tokens=cc_t,
            cache_read_input_tokens=cr_t,
            latest_context_tokens=last_ctx,
        )
    except (OSError, FileNotFoundError):
        return None


def sweep_projects_root(
    projects_root: Path,
    modified_since: float,
    body_role: Optional[str] = None,
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
        cc_identity = resolve_cc_identity(repo_dir.name)
        if not cc_identity:
            continue
        # Body-aware relabel: the orchestrator repo dir is SHARED by the hub
        # (cc-orchestrator, Studio) and the console body (Nazim / orch-console,
        # Mini). The directory alone can't tell them apart — they differ only by
        # which host/body runs the session (ORCH_BODY_ROLE per ORCH-TOPOLOGY-001).
        # When the writer runs under the console body, relabel so Nazim's context
        # lands as its own console row instead of commingling with the hub's gauge.
        if cc_identity == "cc-orchestrator" and body_role == "console":
            cc_identity = "orch-console"
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
                "latest_context_tokens": tokens.latest_context_tokens,
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
                      latest_context_tokens = %s,
                      ended_at = %s
                    WHERE id = %s
                    """,
                    (
                        row["input_tokens"],
                        row["output_tokens"],
                        row["cache_creation_input_tokens"],
                        row["cache_read_input_tokens"],
                        row.get("latest_context_tokens", 0),
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
                       latest_context_tokens,
                       source, has_per_message_detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false)
                    """,
                    (
                        row["cc_identity"], row["session_id"], started_at, started_at,
                        row["input_tokens"], row["output_tokens"],
                        row["cache_creation_input_tokens"], row["cache_read_input_tokens"],
                        row.get("latest_context_tokens", 0),
                        source,
                    ),
                )
            written += 1
    return written
