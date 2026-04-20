"""Launcher auto-identity + repo family resolution.

Composes msgs 315/317/324 per GOVERNANCE-CLEANUP-001 Step 3.
Dual-identity convention: sub-tag (per-instance, agent_status + GUC) +
base (family, agent_messages.from_agent FK-enforced).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


class UnknownRepoError(ValueError):
    """Raised when pwd does not map to a registered agent family.
    Fail-fast per CAI msg 395 Q2 constraint — never silent-fallback."""


def load_family_map(dsn: str) -> dict[str, str]:
    """Build canonical repo-name → base-agent-id map from agents.repo_scope.

    Canonicalization: strip 'wingmen-' prefix so 'wingmen-orchestrator'
    matches filesystem basename 'orchestrator'. Replaces any hardcoded map.
    Raises ValueError if two agent rows claim the same canonical repo
    (indicates agents table corruption — fail loud, not silent-last-wins).

    Called once per launcher boot. Zero drift risk — if agents table changes,
    next launcher picks up the new map automatically.
    """
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, repo_scope FROM agents WHERE id LIKE 'cc-%'"
            )
            rows = cur.fetchall()

    out: dict[str, str] = {}
    for agent_id, repo_list in rows:
        for raw in (repo_list or []):
            canon = raw[len("wingmen-"):] if raw.startswith("wingmen-") else raw
            existing = out.get(canon)
            if existing is not None and existing != agent_id:
                raise ValueError(
                    f"repo {canon!r} claimed by both {existing} and {agent_id} "
                    f"in agents.repo_scope — fix the agents table before launching"
                )
            out[canon] = agent_id
    return out


# Matches trailing worktree-style suffix: -UPPERCASE... or .UPPERCASE... or
# .wt-anything. Lowercase inner hyphens (hifz-companion) do NOT match since
# the second group requires an uppercase start or the literal 'wt'.
_WORKTREE_SUFFIX_RE = re.compile(r"[-.](?:[A-Z][\w-]*|wt[\w-]*)$")


def strip_worktree_suffix(segment: str) -> str:
    """Strip trailing worktree-style suffix from a path segment.

    'orchestrator-LEDGER' → 'orchestrator'
    'orchestrator.wt-qurban' → 'orchestrator'
    'hifz-companion' → 'hifz-companion' (lowercase hyphen, no match)

    CONVENTION (delta-v2 + CAI msg 407): worktree suffixes MUST use either:
        (a) uppercase-initial token:  `-FEATURE`, `-LEDGER`, `-HOTFIX`
        (b) dot-wt prefix form:       `.wt-qurban`, `.wt-abc123`
    Lowercase suffixes (e.g. `orchestrator-hotfix`) are treated as CANONICAL
    repo names, NOT worktrees — they will UnknownRepoError on pwd resolution.
    This trades one convention rule for zero ambiguity between `hifz-companion`
    (canonical, no strip) and `orchestrator-LEDGER` (worktree, stripped).
    """
    return _WORKTREE_SUFFIX_RE.sub("", segment)


def _git_toplevel(pwd: str) -> str | None:
    """Return `git rev-parse --show-toplevel` for pwd, or None if not in a repo."""
    try:
        r = subprocess.run(
            ["git", "-C", pwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        # OSError covers FileNotFoundError (git missing), PermissionError,
        # and non-existent pwd. Never kill the launcher — degrade to walk.
        pass
    return None


def resolve_base_agent_id(pwd: str, family_map: dict[str, str]) -> str:
    """Map an absolute pwd to a registered agents.id base family.

    Algorithm:
      1. Try `git rev-parse --show-toplevel` — get worktree/repo root path.
      2. Take basename, apply strip_worktree_suffix, look up in family_map.
      3. If miss OR no git toplevel: walk pwd components bottom-up, apply
         strip_worktree_suffix at each, first hit wins.
      4. Fail-fast UnknownRepoError if nothing matches.
    """
    # Step 1+2: git toplevel
    toplevel = _git_toplevel(pwd)
    if toplevel:
        basename = strip_worktree_suffix(Path(toplevel).name)
        if basename in family_map:
            return family_map[basename]

    # Step 3: fallback — walk pwd components bottom-up
    for part in reversed(Path(pwd).parts):
        if not part or part == "/":
            continue
        canon = strip_worktree_suffix(part)
        if canon in family_map:
            return family_map[canon]

    raise UnknownRepoError(
        f"pwd {pwd!r} (toplevel={toplevel!r}) is not a registered agent family. "
        f"Known: {sorted(family_map.keys())}"
    )


def pick_sub_tag(base: str, active: list[str]) -> str:
    """Pick the smallest positive integer N such that f'{base}-{N}' is not in active.

    Pure function — no DB. Callers feed it a scanned active list. Ignores
    entries that don't parse as '{base}-<int>' (e.g. legacy bare base, or
    experimental suffixes).
    """
    taken: set[int] = set()
    prefix = f"{base}-"
    for entry in active:
        if not entry.startswith(prefix):
            continue
        suffix = entry[len(prefix):]
        if not suffix.isdigit():
            continue
        taken.add(int(suffix))

    n = 1
    while n in taken:
        n += 1
    return f"{base}-{n}"
