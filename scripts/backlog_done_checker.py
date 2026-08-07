#!/usr/bin/env python3
"""backlog_done_checker.py — evidence-driven auto-complete for operator_backlog.

Operator op#9187/9190: every backlog item has an explicit `done_when` goal; items
that carry a machine-checkable `done_signal` auto-complete (status='done') the
moment the evidence is real — never on anyone's say-so. Items with no signal stay
curated against their stated `done_when` (Nazim marks them, but the goal is now
explicit, not a vibe).

Supported done_signal shapes (jsonb on operator_backlog.done_signal):
  {"type":"deploy_live","url":"https://…","expect_status":200}
        -> HTTP GET the url; done when it returns expect_status (default 200).
  {"type":"pr_merged","repo":"owner/repo","pr":223}
        -> `gh pr view` says merged.
  {"type":"deploy_stage","workstream":"…substring…"}
        -> a row in the `deploys`/work_outputs feed for that workstream is 'live'.
  {"type":"manual"} or absent -> never auto-completes (curated).

On a met signal: set status='done', stamp the evidence into note, log it. Read-
only until then; a signal that errors is treated as NOT met (fail-safe: never
auto-complete on an unverifiable check).

Usage: backlog_done_checker.py [--once] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

_ORCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLL_SEC = 120


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if v:
        return v
    for line in open(os.path.join(_ORCH_DIR, ".env")):
        if line.startswith(("DATABASE_URL=", "SUPABASE_DB_URL=")):
            return line.split("=", 1)[1].strip()
    raise SystemExit("backlog_done_checker: no DATABASE_URL")


def _log(msg: str) -> None:
    print(f"[backlog-done] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# ── signal evaluators (each returns (met: bool, evidence: str)) ───────────────
def _sig_deploy_live(sig: dict) -> "tuple[bool,str]":
    url = sig.get("url")
    want = int(sig.get("expect_status", 200))
    if not url:
        return False, "no url"
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "wingmen-backlog-done"})
        with urllib.request.urlopen(req, timeout=12) as r:
            code = r.status
        return (code == want), f"GET {url} -> {code} (want {want})"
    except Exception as e:
        return False, f"GET {url} error {type(e).__name__}"


def _sig_pr_merged(sig: dict) -> "tuple[bool,str]":
    repo, pr = sig.get("repo"), sig.get("pr")
    if not repo or not pr:
        return False, "missing repo/pr"
    try:
        r = subprocess.run(["gh", "pr", "view", str(pr), "--repo", repo, "--json", "state,mergedAt"],
                           capture_output=True, text=True, timeout=25)
        if r.returncode != 0:
            return False, f"gh rc={r.returncode}"
        d = json.loads(r.stdout or "{}")
        merged = d.get("state") == "MERGED" and bool(d.get("mergedAt"))
        return merged, f"PR {repo}#{pr} state={d.get('state')} mergedAt={d.get('mergedAt')}"
    except Exception as e:
        return False, f"gh error {type(e).__name__}"


def _sig_deploy_stage(conn, sig: dict) -> "tuple[bool,str]":
    ws = sig.get("workstream")
    if not ws:
        return False, "no workstream"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT stage FROM deploys WHERE workstream ILIKE %s ORDER BY updated_at DESC LIMIT 1",
                        ["%" + ws + "%"])
            row = cur.fetchone()
        stage = row[0] if row else None
        return (stage == "live"), f"deploys[{ws}] stage={stage}"
    except Exception as e:
        return False, f"deploys error {type(e).__name__}"


def _evaluate(conn, sig: dict) -> "tuple[bool,str]":
    t = (sig or {}).get("type")
    if t == "deploy_live":
        return _sig_deploy_live(sig)
    if t == "pr_merged":
        return _sig_pr_merged(sig)
    if t == "deploy_stage":
        return _sig_deploy_stage(conn, sig)
    return False, f"unsupported/manual type={t!r}"


def check_once(conn, dry_run: bool) -> int:
    completed = 0
    with conn.cursor() as cur:
        cur.execute("SELECT id, ask, done_signal FROM operator_backlog "
                    "WHERE status NOT IN ('done','dropped') AND done_signal IS NOT NULL")
        rows = cur.fetchall()
    for bid, ask, sig in rows:
        if not sig or sig.get("type") in (None, "manual"):
            continue
        met, evidence = _evaluate(conn, sig)
        if met:
            _log(f"DONE id={bid} '{ask[:40]}' — {evidence}")
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE operator_backlog SET status='done', "
                        "  note = COALESCE(note,'') || %s, updated_at=now() "
                        "WHERE id=%s AND status NOT IN ('done','dropped')",
                        [f" [auto-done {time.strftime('%Y-%m-%d %H:%M')}Z: {evidence}]", bid])
                conn.commit()
            completed += 1
        else:
            _log(f"pending id={bid} '{ask[:40]}' — {evidence}")
    return completed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import psycopg
    dsn = _dsn()
    _log(f"up — evidence-driven backlog auto-complete ({'once' if args.once else f'loop {POLL_SEC}s'}"
         f"{' DRY-RUN' if args.dry_run else ''})")
    while True:
        try:
            with psycopg.connect(dsn, autocommit=False) as conn:
                n = check_once(conn, args.dry_run)
                if n:
                    _log(f"auto-completed {n} item(s)")
        except Exception as e:
            _log(f"cycle error ({type(e).__name__}): {e}")
        if args.once:
            return 0
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    sys.exit(main())
