"""repo_context_writer — keep public.repo_context fresh by polling each repo's
local clone every 15 min (per CAI-RESP-093).

Mechanical fields (always written when readable):
  recent_changes  — last 5 origin/main commit subjects
  deploy_url      — from REPOS.json
  updated_at      — now()
  updated_by      — 'cc-orchestrator' (this writer)

Semantic fields (best-effort STATUS.md parse from origin/main):
  current_phase           — header `Phase: X` line
  blockers                — `## Blocked` / `## Blockers` section content
  known_debt              — `## Known Debt(s)` / `## Technical Debt` section content
  architecture_summary    — `## Architecture` section content (rarely present)

Per CAI-RESP-093: when STATUS.md is missing or malformed for a repo, semantic
fields stay NULL (do not overwrite seeded values with garbage). Mechanical
fields still update so freshness signal isn't lost. Gaps are surfaced via
structured log so missing STATUS.md becomes a legible signal back to that CC
family that session-end discipline slipped.

STATUS.md is read from `origin/main` (after `git fetch`) — not the working tree —
so we capture the canonical, last-pushed content rather than a CC family's
in-flight feature-branch edits.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("wingmen.repo_context_writer")

REPOS_JSON = Path(__file__).parent.parent / "REPOS.json"


# ----------------------------------------------------------------------------
# STATUS.md parsing — pure functions, no DB / no I/O
# ----------------------------------------------------------------------------

# Heading aliases per repo_context column. Lenient — if a repo names a section
# `## Blockers` vs `## Blocked`, both map to the `blockers` column. Matching is
# case-insensitive on the heading text only; section content is preserved verbatim.
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "blockers": ("Blocked", "Blockers"),
    "known_debt": ("Known Debt", "Known Debts", "Known Debts (tracked)", "Technical Debt"),
    "architecture_summary": ("Architecture", "Architecture Summary"),
}

# Header key-value extraction. STATUS.md files conventionally start with
# `Phase: X`, `Deploy URL: X`, `Build Status: X` lines in the first ~10 lines.
_HEADER_PATTERNS: dict[str, re.Pattern] = {
    "current_phase": re.compile(r"^\s*Phase\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE),
}


def parse_status_md(content: str) -> dict[str, object]:
    """Parse STATUS.md content into repo_context-shaped dict.

    Returns a dict with keys {current_phase: str|None, blockers: list[str]|None,
    known_debt: list[str]|None, architecture_summary: str|None}.

    Schema-typed:
    - current_phase + architecture_summary are TEXT (str | None).
    - blockers + known_debt are ARRAY (list[str] | None) — bullet items split.

    Missing fields are NULL (None). Lenient — non-canonical formats produce
    partial results, never exceptions.
    """
    if not content or not content.strip():
        return {k: None for k in ("current_phase", "blockers", "known_debt", "architecture_summary")}

    out: dict[str, object] = {
        "current_phase": None,
        "blockers": None,
        "known_debt": None,
        "architecture_summary": None,
    }

    # Header block — first 20 lines or up to first `## ` heading, whichever sooner.
    lines = content.splitlines()
    header_end = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), min(20, len(lines)))
    header_text = "\n".join(lines[:header_end])
    for col, pat in _HEADER_PATTERNS.items():
        m = pat.search(header_text)
        if m:
            value = m.group(1).strip()
            if value:
                out[col] = value[:500]  # cap to fit repo_context column

    # Section parse — for each canonical column, try each alias heading.
    # Match `## <Alias>` (allowing trailing context like ` (tracked)`).
    for col, aliases in _SECTION_ALIASES.items():
        for alias in aliases:
            section = _extract_section(content, alias)
            if not section:
                continue
            if col in ("blockers", "known_debt"):
                # ARRAY columns — parse bullet list. Lenient: accept '-' or '*'
                # bullets at line start, fall back to single-element array of
                # the whole section if no bullets present.
                items = _parse_bullet_list(section)
                out[col] = items[:50] if items else [section[:500]]
            else:
                out[col] = section[:2000]
            break  # first matching alias wins

    return out


def _parse_bullet_list(text: str) -> list[str]:
    """Extract bullet items from a markdown section. Returns a list of bullet
    contents (without the leading '- ' or '* '). Returns empty list if no
    bullets recognized."""
    items: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if m:
            content = m.group(1).strip()
            if content:
                items.append(content[:500])  # cap per item
    return items


def _extract_section(content: str, heading: str) -> str | None:
    """Return the body text under `## <heading>` up to the next `## ` line.

    Match is case-insensitive on the heading word. Trailing parens / extra text
    on the heading line are tolerated (e.g. `## Known Debts (tracked)` matches
    heading=`Known Debts`). Returns None if heading not found.
    """
    # Match `## <heading>` at line start, allowing trailing chars
    # (case-insensitive). Capture body until next `## ` line or EOF.
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\b[^\n]*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(content)
    if not m:
        return None
    body = m.group(1).strip()
    return body if body else None


# ----------------------------------------------------------------------------
# Repo I/O — git fetch + show origin/main:STATUS.md, recent commits
# ----------------------------------------------------------------------------

async def read_status_md_from_origin_main(repo_path: str) -> str | None:
    """Fetch origin/main and read STATUS.md from there. Returns None if any
    step fails (no clone, no remote, no STATUS.md, etc.)."""
    if not Path(repo_path).is_dir():
        logger.debug(f"repo_context_writer: {repo_path} not a directory; skipping STATUS.md")
        return None

    # Best-effort fetch — ignore failures (network, permissions, missing remote).
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "fetch", "origin", "main", "--quiet",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
    except (asyncio.TimeoutError, OSError) as e:
        logger.debug(f"repo_context_writer: git fetch failed for {repo_path}: {e}")
        # Continue anyway — origin/main may have stale-but-readable content

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "show", "origin/main:STATUS.md",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return None
        return stdout.decode(errors="replace")
    except (asyncio.TimeoutError, OSError) as e:
        logger.debug(f"repo_context_writer: git show failed for {repo_path}: {e}")
        return None


async def get_recent_changes(repo_path: str, limit: int = 5) -> str:
    """Return the last N origin/main commit subjects, newline-joined.
    Empty string if not a git repo or fetch fails."""
    if not Path(repo_path).is_dir():
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "log", "origin/main",
            "--oneline", f"-{limit}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return ""
        return stdout.decode(errors="replace").strip()
    except (asyncio.TimeoutError, OSError):
        return ""


# ----------------------------------------------------------------------------
# Main entry — iterate REPOS.json, upsert repo_context per repo
# ----------------------------------------------------------------------------

def _load_repos() -> list[dict]:
    with open(REPOS_JSON) as f:
        return json.load(f)["repos"]


def _resolve_path(raw_path: str) -> str:
    return os.path.expanduser(raw_path)


async def update_repo_contexts(supabase) -> None:
    """Poll each repo in REPOS.json, upsert public.repo_context.

    Mechanical fields always update if origin/main reachable.
    Semantic fields parsed from origin/main:STATUS.md when present + parseable.

    Called from wingmen_orch.py main loop every 15 minutes (counter >= 30 at
    POLL_INTERVAL=30s). Per-repo failures are logged + don't abort the sweep.
    """
    repos = _load_repos()
    logger.debug(f"repo_context_writer: sweeping {len(repos)} repos")

    written = 0
    skipped = 0
    semantic_gaps: list[str] = []

    for repo in repos:
        name = repo.get("name")
        if not name or repo.get("status") != "active":
            continue
        local_path = _resolve_path(repo.get("local_path", ""))

        recent_changes = await get_recent_changes(local_path)
        status_content = await read_status_md_from_origin_main(local_path)

        if status_content is None:
            semantic_fields: dict[str, str | None] = {
                k: None for k in ("current_phase", "blockers", "known_debt", "architecture_summary")
            }
            semantic_gaps.append(f"{name}: no origin/main STATUS.md")
        else:
            semantic_fields = parse_status_md(status_content)
            missing = [k for k, v in semantic_fields.items() if v is None]
            if len(missing) == 4:
                semantic_gaps.append(f"{name}: STATUS.md present but no recognized sections")
            elif missing:
                semantic_gaps.append(f"{name}: STATUS.md missing fields {missing}")

        # Build the upsert payload. Mechanical fields always written; semantic
        # fields conditionally — only overwrite repo_context column with NULL
        # if we DELIBERATELY parsed and got nothing. We do overwrite (not preserve
        # seeded values) because the writer is now the source of truth per CAI-RESP-093.
        row = {
            "repo": name,
            "recent_changes": recent_changes[:1500] if recent_changes else None,
            "deploy_url": repo.get("deploy_url"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": "cc-orchestrator",
            **semantic_fields,
        }

        try:
            await supabase.table("repo_context").upsert(row, on_conflict="repo").execute()
            written += 1
        except Exception as e:
            logger.error(f"repo_context_writer: upsert failed for {name}: {e}")
            skipped += 1

    logger.info(
        f"repo_context_writer: swept {len(repos)} repos, {written} written, {skipped} failed"
    )
    if semantic_gaps:
        logger.info(f"repo_context_writer: semantic gaps — {'; '.join(semantic_gaps)}")
