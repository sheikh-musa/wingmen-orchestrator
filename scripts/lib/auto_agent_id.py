"""Launcher auto-identity + repo family resolution.

Composes msgs 315/317/324 per GOVERNANCE-CLEANUP-001 Step 3.
Dual-identity convention: sub-tag (per-instance, agent_status + GUC) +
base (family, agent_messages.from_agent FK-enforced).

INVARIANT: this module uses psycopg exclusively for DB I/O. No supabase-py
imports. See test_auto_agent_id_does_not_import_supabase_py for enforcement.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


# Advisory-lock registry (see docs/lock-namespace.md).
# Identity/scheduling range: 1000–1099. Reserve new keys there.
_ALLOC_LOCK_ID = 1001  # "cc-agent-id-alloc" — allocator critical section
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL_SECONDS = 0.5
_LOCK_RETRY_COUNT = 10  # 10 × 0.5s = 5s ceiling

# G3 (CAI-RESP-053): 20 concurrent sub-tags per base is pathology threshold.
# 4 families × 20 = 80 concurrent identities — anything approaching this is
# runaway launchd/watchdog, not legitimate concurrency. Fail loud.
_MAX_SUB_TAGS_PER_BASE = 20


class NamespaceExhaustedError(RuntimeError):
    """Raised when pick_sub_tag is asked to allocate past _MAX_SUB_TAGS_PER_BASE.
    Operator should investigate rogue launchd/watchdog rather than raising the cap."""


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
    if n > _MAX_SUB_TAGS_PER_BASE:
        siblings = [f"{base}-{k}" for k in sorted(taken)]
        raise NamespaceExhaustedError(
            f"{base} exhausted ({_MAX_SUB_TAGS_PER_BASE} concurrent sub-tags). "
            f"Likely runaway launchd/watchdog. "
            f"Run `ps aux | grep launch_dangerous_cc` and cull. "
            f"Siblings: {siblings}"
        )
    return f"{base}-{n}"


class LockTimeoutError(RuntimeError):
    """Raised when pg_try_advisory_xact_lock fails to acquire within retry budget.

    Delta-v2 L2: `pg_advisory_xact_lock` blocks indefinitely if another
    launcher's TX wedges. We prefer a bounded wait + pg_locks diagnostic so
    the operator sees who holds the key rather than hanging forever.
    """


@dataclass(frozen=True)
class AllocResult:
    """Result of allocate_sub_tag_and_register: chosen sub-tag + siblings seen pre-allocation."""
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
            # Bounded lock acquisition: _LOCK_RETRY_COUNT × _LOCK_POLL_INTERVAL_SECONDS ceiling.
            acquired = False
            for attempt in range(_LOCK_RETRY_COUNT):
                cur.execute(
                    "SELECT pg_try_advisory_xact_lock(%s)",
                    (_ALLOC_LOCK_ID,),
                )
                if cur.fetchone()[0]:
                    acquired = True
                    break
                if attempt < _LOCK_RETRY_COUNT - 1:
                    time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
            if not acquired:
                # Diagnostic: who holds our key?
                cur.execute(
                    """
                    SELECT pid, granted, query_start, state
                      FROM pg_locks l
                      JOIN pg_stat_activity a ON a.pid = l.pid
                     WHERE locktype = 'advisory'
                       AND objid = %s
                    """,
                    (_ALLOC_LOCK_ID,),
                )
                holders = cur.fetchall()

                # A4 (CAI-RESP-053): flush diagnostic to disk before raising.
                # Launcher stderr may be redirected by launchd; a dedicated
                # file gives the operator a forensic trail.
                import datetime as _dt
                import glob as _glob
                import os as _os
                import traceback as _traceback
                _iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                _diag_path = f"/tmp/cc_lock_timeout_{_iso}.log"
                try:
                    with open(_diag_path, "a") as _fh:
                        _fh.write(f"timestamp: {_iso}\n")
                        _fh.write(f"base: {base}\n")
                        _fh.write(f"repo: {repo}\n")
                        _fh.write(f"lock_id: {_ALLOC_LOCK_ID}\n")
                        _fh.write(f"timeout_seconds: {_LOCK_TIMEOUT_SECONDS}\n")
                        _fh.write(f"holders: {holders}\n")
                        _fh.write("stack:\n")
                        _fh.write("".join(_traceback.format_stack()))
                        _fh.flush()
                        _os.fsync(_fh.fileno())
                except OSError:
                    pass  # disk full / permission — swallow; primary signal is the raise

                # SA1 (CAI-RESP-054): keep newest-40, treat oldest-20 of those
                # as overflow generation. Bursty lock-timeout storms no longer
                # evict their own evidence before the operator sees it.
                # Per-file is small (~KB of stack+header), so 40 files ≪ 20MB cap.
                try:
                    _existing = sorted(_glob.glob("/tmp/cc_lock_timeout_*.log"))
                    for _old in _existing[:-40]:
                        try:
                            _os.unlink(_old)
                        except OSError:
                            pass
                except OSError:
                    pass

                raise LockTimeoutError(
                    f"advisory lock AGENT_ID_ALLOC (id={_ALLOC_LOCK_ID}) "
                    f"held >{_LOCK_TIMEOUT_SECONDS}s. Holders: {holders}"
                )

            cur.execute(
                """
                SELECT agent_id FROM agent_status
                 WHERE agent_id LIKE %s
                   AND base_agent_id = %s
                   AND last_heartbeat > now() - (%s * interval '1 minute')
                   AND status != 'offline'
                """,
                (f"{base}-%", base, stale_cutoff_minutes),
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
                  (agent_id, base_agent_id, status, current_task, scope_repos, last_heartbeat, updated_at)
                VALUES (%s, %s, 'working', 'session-launch', ARRAY[%s]::text[], now(), now())
                ON CONFLICT (agent_id) DO UPDATE SET
                  base_agent_id = EXCLUDED.base_agent_id,
                  status = 'working',
                  current_task = 'session-launch',
                  scope_repos = ARRAY[%s]::text[],
                  last_heartbeat = now(),
                  updated_at = now()
                """,
                (sub_tag, base, repo, repo),
            )
        conn.commit()  # releases advisory lock

    return AllocResult(sub_tag=sub_tag, siblings=siblings)


def scan_overlap_siblings(
    base: str,
    scope_repo: str,
    dsn: str,
    exclude_sub_tag: str,
    stale_cutoff_minutes: int = 30,
) -> list[tuple[str, int]]:
    """Return `(agent_id, heartbeat_age_seconds)` for active sub-tags in
    `base` family whose scope_repos contains `scope_repo`, excluding
    `exclude_sub_tag` (the caller itself).

    Soft-warning helper per CAI msg 395 Q3-C — prints a pre-launch overlap
    notice if 2+ CCs in the same family are about to edit the same repo.
    Read-only, no lock, no UPSERT.

    Delta-v2 non-load-bearing #4: include heartbeat age so operator sees
    actionable recency at a glance (e.g. `cc-ihsanos-2 (3s ago)` vs
    `cc-ihsanos-5 (847s ago)`).
    """
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT agent_id,
                       EXTRACT(EPOCH FROM (now() - last_heartbeat))::int AS age_s
                  FROM agent_status
                 WHERE agent_id LIKE %s
                   AND agent_id != %s
                   AND last_heartbeat > now() - (%s * interval '1 minute')
                   AND status != 'offline'
                   AND %s = ANY(scope_repos)
                """,
                (f"{base}-%", exclude_sub_tag, stale_cutoff_minutes, scope_repo),
            )
            return [(r[0], int(r[1])) for r in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: emit JSON {sub_tag, base, siblings, overlap_warnings}.

    Invoked by scripts/launch_dangerous_cc.sh. Exits 1 on UnknownRepoError
    or LockTimeoutError with human-readable stderr.

    Delta-v2 checkpoint: CLI loads the family map itself (not launcher-side).
    Keeps bash pure — shell ships only --pwd/--repo/--dsn and the CLI owns
    the map-shape surface.
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.lib.auto_agent_id",
        description="Allocate a sub-tag agent identity within a registered family.",
    )
    parser.add_argument("--pwd", required=True, help="caller working directory")
    parser.add_argument("--repo", required=True, help="repo name for scope_repos")
    parser.add_argument("--dsn", required=True, help="Postgres DSN")
    parser.add_argument("--stale-minutes", type=int, default=30)
    parser.add_argument(
        "--base-override",
        default=None,
        help="skip pwd→family resolution, use this base (test hook only)",
    )
    args = parser.parse_args(argv)

    try:
        # Data-driven map (delta-v2 L1-B2): load from agents.repo_scope each boot.
        # Drift from hardcoded constants is structurally impossible.
        if args.base_override:
            base = args.base_override
        else:
            family_map = load_family_map(args.dsn)
            base = resolve_base_agent_id(args.pwd, family_map)
    except UnknownRepoError as e:
        sys.stderr.write(f"UnknownRepoError: {e}\n")
        return 1
    except Exception as e:
        # Bad DSN / DB unreachable / etc. Fail loud — operator needs to see
        # the real cause, not a misleading 'not registered' message.
        sys.stderr.write(f"DatabaseError: {type(e).__name__}: {e}\n")
        return 1

    try:
        result = allocate_sub_tag_and_register(
            base=base,
            dsn=args.dsn,
            repo=args.repo,
            stale_cutoff_minutes=args.stale_minutes,
        )
    except LockTimeoutError as e:
        sys.stderr.write(f"LockTimeoutError: {e}\n")
        return 1

    overlaps = scan_overlap_siblings(
        base=base,
        scope_repo=args.repo,
        dsn=args.dsn,
        exclude_sub_tag=result.sub_tag,
        stale_cutoff_minutes=args.stale_minutes,
    )

    json.dump(
        {
            "sub_tag": result.sub_tag,
            "base": base,
            "siblings": list(result.siblings),
            # list of [agent_id, heartbeat_age_s] pairs (JSON-serialised tuples).
            "overlap_warnings": [[aid, age] for (aid, age) in overlaps],
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
