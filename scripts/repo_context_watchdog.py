#!/usr/bin/env python3
"""repo_context_watchdog.py — keep repo_context / boot_briefing from silently freezing.

Fix #2 (durable half). The repo_context updater
(`nervous_system.repo_context_writer.update_repo_contexts`) only ever ran inside
`wingmen_orch.py`'s main loop. When that process died (13+ days on 2026-07-22),
NOTHING refreshed `public.repo_context`, so `boot_briefing` served stale rows to
every booting agent — with no signal that it had frozen. The manual unfreeze was
run by hand today; this watchdog is the DURABLE guarantee it never freezes
silently again.

On each run it:
  (a) calls `update_repo_contexts(supabase)` — the same sweep the dead orch loop
      used to run every 15 min (git fetch per active repo -> upsert repo_context);
  (b) AFTER updating, re-reads `repo_context.updated_at` per active repo and, if
      any active repo is older than FRESH_THRESHOLD_S (default 2h), pages the
      operator with a degrade alert (the write silently failed to land, or a repo
      dropped out of the sweep) — a secondary assertion the row actually moved;
  (c) is idempotent + safe to run every 15 min (StartInterval 900s). The upsert
      is on_conflict=repo, so re-runs just re-stamp updated_at.

Robustness (mirrors scripts/context_health_watchdog.py — the fleet doctrine that
a watchdog which fails silently is worse than none):
  - SELF-CONTAINED imports: under launchd there is NO PYTHONPATH, so we insert
    _ORCH_DIR on sys.path and load_dotenv(.env) ourselves — never depend on the
    env for our own package imports.
  - GRACEFUL DEGRADE: the operator page prefers scripts/nazim_send.sh, falls back
    to scripts/tg_send.sh, then stderr — a missing send-script never crashes the
    guard.
  - TOP-LEVEL CRASH -> operator page: the __main__ dead-man's-switch pages the
    operator via a dependency-free subprocess path if ANYTHING throws, so the
    failure of the guard is itself surfaced (a frozen boot_briefing must never be
    invisible again).

Usage:
    repo_context_watchdog.py              # sweep + freshness check + page on stale
    repo_context_watchdog.py --json       # machine-readable classification
    repo_context_watchdog.py --no-update  # freshness check only (skip the sweep)
    repo_context_watchdog.py --alert-stdout   # print the degrade page instead of sending
    # Env: REPO_CTX_WD_THRESHOLD_S overrides the staleness threshold (default 7200).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_ORCH_DIR = Path(__file__).resolve().parent.parent
# Self-contained package imports: under launchd there is NO PYTHONPATH, so a bare
# `import nervous_system...` ModuleNotFound's and the whole run crashes silently.
# Never depend on the env for our own imports (same lesson as context_health_watchdog).
if str(_ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCH_DIR))
load_dotenv(_ORCH_DIR / ".env")

REPOS_JSON = _ORCH_DIR / "REPOS.json"

# Staleness threshold: an active repo whose repo_context.updated_at is older than
# this AFTER a sweep means the write didn't land -> degrade. 2h is generous for a
# 15-min cadence (8 chances to refresh before we page), so a page means a real,
# persistent freeze — not a single transient miss.
_FRESH_THRESHOLD_S = int(os.environ.get("REPO_CTX_WD_THRESHOLD_S", str(2 * 3600)))


# --------------------------------------------------------------------------- #
# Pure freshness logic — no DB / no I/O, unit-testable in isolation.
# --------------------------------------------------------------------------- #

@dataclass
class RepoFreshness:
    repo: str
    updated_at: Optional[str]   # ISO string as last seen, or None if no row
    age_s: Optional[int]        # seconds since updated_at, or None if missing
    stale: bool                 # True -> missing row OR age_s > threshold
    reason: str                 # "" | "no repo_context row" | "stale (age > threshold)"


def active_repo_names(repos: list[dict]) -> list[str]:
    """Names of repos the writer sweeps — mirrors update_repo_contexts' filter
    (status == 'active', name present). These are the repos whose freshness the
    watchdog asserts."""
    return [
        r["name"]
        for r in repos
        if r.get("name") and r.get("status") == "active"
    ]


def evaluate_freshness(
    updated_at_by_repo: dict[str, Optional[datetime]],
    active_repos: list[str],
    now: datetime,
    threshold_s: int = _FRESH_THRESHOLD_S,
) -> list[RepoFreshness]:
    """Classify each active repo's repo_context freshness. PURE.

    Args:
      updated_at_by_repo: repo -> its repo_context.updated_at (tz-aware datetime),
        or None when the repo has no row at all.
      active_repos: the set of repos the writer is expected to keep fresh.
      now: the reference time (tz-aware) to age against.
      threshold_s: max allowed age in seconds before a repo is 'stale'.

    Returns one RepoFreshness per active repo, sorted stale-first then oldest-first.
    A repo with no row (missing key or None value) is stale with age_s=None.
    """
    out: list[RepoFreshness] = []
    for repo in active_repos:
        ts = updated_at_by_repo.get(repo)
        if ts is None:
            out.append(RepoFreshness(repo, None, None, True, "no repo_context row"))
            continue
        # Normalise to tz-aware UTC so subtraction never raises on naive/aware mix.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_s = int((now - ts).total_seconds())
        if age_s > threshold_s:
            out.append(RepoFreshness(repo, ts.isoformat(), age_s, True, "stale (age > threshold)"))
        else:
            out.append(RepoFreshness(repo, ts.isoformat(), age_s, False, ""))
    # stale first; within each group, oldest (largest age; missing => sentinel) first.
    out.sort(key=lambda r: (not r.stale, -(r.age_s if r.age_s is not None else 1 << 62)))
    return out


def _parse_ts(raw: object) -> Optional[datetime]:
    """Parse a Supabase timestamptz string into a tz-aware datetime. Lenient:
    returns None on anything unparseable rather than raising."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    # Postgres/PostgREST emits e.g. '2026-07-22T10:00:00.123456+00:00' or a
    # trailing 'Z'. fromisoformat handles the former; swap Z for +00:00.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# DB I/O — build the async supabase client + read repo_context freshness.
# --------------------------------------------------------------------------- #

def _load_repos() -> list[dict]:
    with open(REPOS_JSON) as f:
        return json.load(f)["repos"]


async def _make_supabase():
    """Build the async supabase client the same way wingmen_orch.get_supabase
    does (SUPABASE_URL / SUPABASE_SERVICE_KEY). Self-contained: imports supabase
    directly rather than importing wingmen_orch (whose module import has heavy
    side effects)."""
    from supabase import acreate_client  # local import — keep module import cheap
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return await acreate_client(url, key)


async def fetch_updated_at(supabase) -> dict[str, Optional[datetime]]:
    """Read repo -> repo_context.updated_at (tz-aware) for every row present."""
    resp = await supabase.table("repo_context").select("repo, updated_at").execute()
    rows = resp.data or []
    out: dict[str, Optional[datetime]] = {}
    for row in rows:
        name = row.get("repo")
        if name:
            out[name] = _parse_ts(row.get("updated_at"))
    return out


# --------------------------------------------------------------------------- #
# Operator page — graceful degrade: nazim_send.sh -> tg_send.sh -> stderr.
# --------------------------------------------------------------------------- #

def _page_operator(text: str) -> None:
    """Deliver a degrade page to the operator's phone. Prefers Nazim's own
    operator voice (scripts/nazim_send.sh); falls back to scripts/tg_send.sh, then
    stderr. A missing send-script must NEVER crash the watchdog.

    REPO_CTX_WD_ALERT_STDOUT=1 prints instead of sending (for testing the decision
    logic without paging the operator)."""
    if os.environ.get("REPO_CTX_WD_ALERT_STDOUT") == "1":
        print("[ALERT-would-send]\n" + text + "\n")
        return
    for script in ("nazim_send.sh", "tg_send.sh"):
        path = _ORCH_DIR / "scripts" / script
        if not path.exists():
            continue
        try:
            subprocess.run([str(path), text], timeout=30, cwd=str(_ORCH_DIR))
            return
        except Exception as e:  # pragma: no cover — delivery must not crash the guard
            print(f"[repo-ctx-wd] page via {script} failed: {e}", file=sys.stderr)
    print(f"[repo-ctx-wd] NO send-script available; degrade page NOT delivered:\n{text}",
          file=sys.stderr)


def _degrade_text(stale: list[RepoFreshness], threshold_s: int) -> str:
    """Compose the ELI5 degrade page for stale repos. Falls back to a plain string
    if the formatter import fails (a delivered plain alert beats a pretty crash)."""
    names = ", ".join(r.repo for r in stale)
    hrs = threshold_s / 3600
    detail = "; ".join(
        f"{r.repo}: {r.reason}" + (f" (age {r.age_s}s)" if r.age_s is not None else "")
        for r in stale
    )
    try:
        from nervous_system.alert_format import format_alert
    except Exception:
        return (f"🚨 repo_context frozen: {names}\n"
                f"boot_briefing is serving stale rows for {names} — after a sweep they are still "
                f"older than {hrs:.0f}h, so the updater isn't landing writes. Booting agents get "
                f"stale context. Check the repo_context_watchdog run + repo_context_writer.\n"
                f"Detail: {detail}")
    return format_alert(
        icon="🚨",
        title=f"repo_context frozen for {len(stale)} repo(s)",
        what=f"After a refresh sweep, repo_context is still stale (>{hrs:.0f}h old) for: {names}.",
        why="boot_briefing serves these stale rows, so every booting agent gets outdated repo context — the exact silent freeze this watchdog exists to catch.",
        do="Check the repo_context_watchdog launchd job + nervous_system.repo_context_writer; the upsert isn't landing. Manual unfreeze: run update_repo_contexts against the DB.",
        detail=detail,
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

async def run(do_update: bool = True, threshold_s: int = _FRESH_THRESHOLD_S) -> dict:
    """One watchdog cycle: (optionally) sweep, then assert freshness. Returns a
    machine-readable result dict; pages the operator on any stale active repo."""
    supabase = await _make_supabase()
    update_ok = True
    update_error = None
    if do_update:
        try:
            from nervous_system.repo_context_writer import update_repo_contexts
            await update_repo_contexts(supabase)
        except Exception as e:
            # A failed sweep is itself a degrade — surface it, but STILL run the
            # freshness check (the last-known rows may already be stale).
            update_ok = False
            update_error = repr(e)

    repos = _load_repos()
    active = active_repo_names(repos)
    updated_at_by_repo = await fetch_updated_at(supabase)
    now = datetime.now(timezone.utc)
    rows = evaluate_freshness(updated_at_by_repo, active, now, threshold_s)
    stale = [r for r in rows if r.stale]

    if stale or not update_ok:
        if not update_ok:
            _page_operator(
                f"🚨 repo_context sweep FAILED: {update_error}\n"
                f"The repo_context updater threw during its scheduled run, so boot_briefing "
                f"rows won't refresh. Check nervous_system.repo_context_writer + the watchdog log."
            )
        if stale:
            _page_operator(_degrade_text(stale, threshold_s))

    return {
        "update_ok": update_ok,
        "update_error": update_error,
        "active_repos": active,
        "threshold_s": threshold_s,
        "rows": [asdict(r) for r in rows],
        "stale": [r.repo for r in stale],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-update", action="store_true",
                    help="skip the sweep; freshness check only")
    ap.add_argument("--alert-stdout", action="store_true",
                    help="print the degrade page instead of paging the operator")
    args = ap.parse_args()

    if args.alert_stdout:
        os.environ["REPO_CTX_WD_ALERT_STDOUT"] = "1"

    result = asyncio.run(run(do_update=not args.no_update, threshold_s=_FRESH_THRESHOLD_S))

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    hrs = result["threshold_s"] / 3600
    print(f"[repo-ctx-wd] update={'ok' if result['update_ok'] else 'FAILED'} "
          f"threshold={hrs:.0f}h active_repos={len(result['active_repos'])}")
    if not result["update_ok"]:
        print(f"[repo-ctx-wd] sweep error: {result['update_error']}")
    for r in result["rows"]:
        flag = "STALE" if r["stale"] else "fresh"
        age = f"{r['age_s']}s" if r["age_s"] is not None else "n/a"
        print(f"  {flag:5} {r['repo']:24} age={age:>10}  {r['reason']}")
    if result["stale"]:
        print(f"[repo-ctx-wd] paged operator — stale: {', '.join(result['stale'])}")
    return 0


if __name__ == "__main__":
    # Dead-man's-switch: a watchdog that dies silently is worse than none. If the
    # sweep/freshness path throws in a way run() didn't catch (e.g. missing env,
    # DB unreachable, import error), page the operator via the dependency-free
    # subprocess path so the failure of the guard is itself surfaced — a frozen
    # boot_briefing must never be invisible again.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _e:
        import traceback
        traceback.print_exc()
        try:
            _page_operator(
                f"🐛 repo_context watchdog CRASHED — boot_briefing freshness is NOT being "
                f"guarded right now: {_e}. Fix before relying on repo_context staying fresh."
            )
        except Exception:
            pass
        sys.exit(1)
