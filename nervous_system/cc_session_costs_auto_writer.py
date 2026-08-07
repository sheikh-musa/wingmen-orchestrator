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


def resolve_cc_identity(dir_name: str, extra: "Optional[dict]" = None) -> Optional[str]:
    """cc_identity for a ~/.claude/projects dir name, or None if unmapped.

    Order: the ratified _DIR_TO_CC families first (so nothing here can shadow
    a canonical identity), then the static lane map, then any DYNAMIC lane map
    (`extra`, from fleet_lanes — see lane_dir_map_from_fleet_lanes). Dynamic comes
    LAST so a verified static entry always wins; the dynamic map only FILLS GAPS.
    All tables are tried on the raw name and on the host-normalised form.
    """
    tables = [_DIR_TO_CC, _LANE_DIR_TO_CC]
    if extra:
        tables.append(extra)
    for table in tables:
        hit = table.get(dir_name) or table.get(_canonical_dir(dir_name))
        if hit:
            return hit
    return None


def lane_dir_map_from_fleet_lanes(dsn: str) -> dict:
    """Build a {claude-projects-dir -> cc_identity} map DYNAMICALLY from
    fleet_lanes.worktree_path, so a newly-registered worker lane is MEASURED
    without a hand-edit to the static _LANE_DIR_TO_CC (which drifts — the same
    stale-registry coverage gap that hid 8 worker lanes from the context gauge,
    op#15406). A claude ~/.claude/projects dir name is the worktree path with
    '/' -> '-'.

    SAME CONTAINMENT as _LANE_DIR_TO_CC (deliberate): this map is consumed ONLY by
    this cost/context writer's resolution (passed as `extra` to resolve_cc_identity)
    — it NEVER widens _DIR_TO_CC, so it can't enrol a lane into the
    autonomous_loop_detector / watchdog KILL paths. Singletons are excluded (they
    resolve canonically via _DIR_TO_CC). Fail-safe: any DB error returns {} (no
    dynamic coverage) rather than crashing the writer."""
    import psycopg
    out: dict = {}
    try:
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            # Every worktree row (NOT distinct-on-base): a multi-worktree FAMILY
            # (the irsyad perimeter) has several worktrees under one base, and ALL
            # of them must resolve to that base — distinct-on-base dropped the
            # non-first perimeter worktrees to None (op#10550). The sub_tag map
            # then distinguishes the instances.
            cur.execute(
                "SELECT base_agent_id, worktree_path FROM fleet_lanes "
                "WHERE worktree_path IS NOT NULL AND base_agent_id NOT IN "
                "  ('cc-orchestrator','cai','orch-console','cc-fleet-health')")
            for base, wt in cur.fetchall():
                d = _mangle_worktree(wt)
                out[d] = base
                out[_canonical_dir(d)] = base
    except Exception:
        return {}
    return out


def _mangle_worktree(wt: str) -> str:
    """A worktree abs-path -> its ~/.claude/projects dir name. Claude replaces
    BOTH '/' AND '.' with '-' (so a `.wt-coord` worktree becomes `-wt-coord`) —
    replacing only '/' silently dropped every dotted-worktree lane (the irsyad
    perimeter wt-coord/prog1/prog2), op#10550."""
    return wt.replace("/", "-").replace(".", "-")


def lane_subtag_map_from_fleet_lanes(dsn: str) -> dict:
    """Build a {claude-projects-dir -> sub_tag} map for MULTI-LANE FAMILIES
    (op#10550). A perimeter (e.g. cc-irsyad's irsyad / irsyad-prog1 / -prog2 /
    -coord — ONE base_agent_id, distinct worktrees) collapses to a single
    cc_identity in cc_session_costs, so all its instances showed the SAME context
    gauge. Recording each worktree with a distinct sub_tag (= its fleet_lanes.lane)
    lets the console key the gauge by (cc_identity, sub_tag) and show per-instance
    context. Only families (base with >1 lane row) get a sub_tag; a solo lane maps
    to no sub_tag (unchanged). Fail-safe: {} on any DB error."""
    import psycopg
    import collections
    out: dict = {}
    try:
        with psycopg.connect(dsn, connect_timeout=15) as c, c.cursor() as cur:
            cur.execute(
                "SELECT base_agent_id, lane, worktree_path FROM fleet_lanes "
                "WHERE worktree_path IS NOT NULL AND base_agent_id NOT IN "
                "  ('cc-orchestrator','cai','orch-console','cc-fleet-health')")
            rows = cur.fetchall()
        by_base = collections.Counter(r[0] for r in rows)
        for base, lane, wt in rows:
            if by_base[base] <= 1:
                continue  # solo lane — no sub_tag needed
            d = _mangle_worktree(wt)
            out[d] = lane
            out[_canonical_dir(d)] = lane
    except Exception:
        return {}
    return out


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
    extra_dir_map: Optional[dict] = None,
    subtag_map: Optional[dict] = None,
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
        cc_identity = resolve_cc_identity(repo_dir.name, extra=extra_dir_map)
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
        # sub_tag distinguishes instances of a multi-lane FAMILY (op#10550) so the
        # console can show per-instance context; None for a solo lane (unchanged).
        sub_tag = None
        if subtag_map:
            sub_tag = subtag_map.get(repo_dir.name) or subtag_map.get(_canonical_dir(repo_dir.name))
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
                "sub_tag": sub_tag,
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
                      sub_tag = %s,
                      ended_at = %s
                    WHERE id = %s
                    """,
                    (
                        row["input_tokens"],
                        row["output_tokens"],
                        row["cache_creation_input_tokens"],
                        row["cache_read_input_tokens"],
                        row.get("latest_context_tokens", 0),
                        row.get("sub_tag"),
                        started_at,
                        existing[0],
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO cc_session_costs
                      (cc_identity, sub_tag, session_id, started_at, ended_at,
                       input_tokens, output_tokens,
                       cache_creation_input_tokens, cache_read_input_tokens,
                       latest_context_tokens,
                       source, has_per_message_detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false)
                    """,
                    (
                        row["cc_identity"], row.get("sub_tag"),
                        row["session_id"], started_at, started_at,
                        row["input_tokens"], row["output_tokens"],
                        row["cache_creation_input_tokens"], row["cache_read_input_tokens"],
                        row.get("latest_context_tokens", 0),
                        source,
                    ),
                )
            written += 1
    return written
