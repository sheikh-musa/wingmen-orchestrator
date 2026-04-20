"""Launcher auto-identity + repo family resolution.

Composes msgs 315/317/324 per GOVERNANCE-CLEANUP-001 Step 3.
Dual-identity convention: sub-tag (per-instance, agent_status + GUC) +
base (family, agent_messages.from_agent FK-enforced).
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
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


class LockTimeoutError(RuntimeError):
    """Raised when pg_try_advisory_xact_lock fails to acquire within retry budget.

    Delta-v2 L2: `pg_advisory_xact_lock` blocks indefinitely if another
    launcher's TX wedges. We prefer a bounded wait + pg_locks diagnostic so
    the operator sees who holds the key rather than hanging forever.
    """


@dataclass(frozen=True)
class AllocResult:
    sub_tag: str
    siblings: list[str]  # active siblings *before* this allocation


def allocate_sub_tag_and_register(
    base: str,
    dsn: str,
    repo: str,
    stale_cutoff_minutes: int = 30,
) -> AllocResult:
    """Atomically: acquire global advisory lock, scan active siblings of `base`,
    pick next-free N, UPSERT agent_status for the picked sub-tag with GUC set.

    The lock is held across scan + UPSERT so two concurrent launchers cannot
    pick the same N — one commits, the other sees the fresh row on rescan.

    Lock strategy (delta-v2 L2): `pg_try_advisory_xact_lock` with 500ms poll
    for up to 5s. On timeout, query pg_locks for the holder and raise
    LockTimeoutError with the diagnostic attached.

    Args:
        base: registered agents.id family (e.g. 'cc-ihsanos').
        dsn: Postgres connection string with the GUC-capable user.
        repo: repo name for scope_repos (single-element array for now).
        stale_cutoff_minutes: rows whose last_heartbeat is older than this
            count as reclaimable (their N is considered free).

    Returns:
        AllocResult(sub_tag, siblings_seen_before_alloc).

    Raises:
        LockTimeoutError: advisory lock not acquired within 5s.
    """
    import psycopg

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Bounded lock acquisition: 10 × 500ms = 5s ceiling.
            acquired = False
            for _ in range(10):
                cur.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                    ("cc-agent-id-alloc",),
                )
                if cur.fetchone()[0]:
                    acquired = True
                    break
                time.sleep(0.5)
            if not acquired:
                # Diagnostic: who holds our key?
                cur.execute(
                    """
                    SELECT pid, granted, query_start, state
                      FROM pg_locks l
                      JOIN pg_stat_activity a ON a.pid = l.pid
                     WHERE locktype = 'advisory'
                       AND objid = hashtext('cc-agent-id-alloc')::bigint
                    """
                )
                holders = cur.fetchall()
                raise LockTimeoutError(
                    f"advisory lock 'cc-agent-id-alloc' held >5s. "
                    f"Holders: {holders}"
                )

            cur.execute(
                """
                SELECT agent_id FROM agent_status
                 WHERE agent_id LIKE %s
                   AND last_heartbeat > now() - (%s * interval '1 minute')
                   AND status != 'offline'
                """,
                (f"{base}-%", stale_cutoff_minutes),
            )
            siblings = [r[0] for r in cur.fetchall()]
            sub_tag = pick_sub_tag(base, siblings)

            # Still inside TX with lock held — UPSERT agent_status.
            # GUC must equal NEW.agent_id (sub-tag) per ARCH-035 trigger.
            cur.execute(
                "SELECT set_config('app.current_agent_id', %s, true)",
                (sub_tag,),
            )
            cur.execute(
                """
                INSERT INTO agent_status
                  (agent_id, status, current_task, scope_repos, last_heartbeat, updated_at)
                VALUES (%s, 'working', 'session-launch', ARRAY[%s]::text[], now(), now())
                ON CONFLICT (agent_id) DO UPDATE SET
                  status = 'working',
                  current_task = 'session-launch',
                  scope_repos = ARRAY[%s]::text[],
                  last_heartbeat = now(),
                  updated_at = now()
                """,
                (sub_tag, repo, repo),
            )
        conn.commit()  # releases advisory lock

    return AllocResult(sub_tag=sub_tag, siblings=siblings)
