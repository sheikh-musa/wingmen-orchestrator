"""Write a repo_snapshot row after a successful git push.

ARCH-024 Phase 2: Collects structural metadata from the caller's repo
and writes a row to repo_snapshot in ops Supabase.

Usage:
    python -m scripts.write_repo_snapshot --repo ihsanos --dir /path/to/repo
    python -m scripts.write_repo_snapshot --repo wingmen-orchestrator --dir /path/to/orch

What it captures (no file content, structural shape only):
  - Latest commit SHA + timestamp
  - Total LOC (git-tracked lines only)
  - File count
  - Test count (files matching test_*.py, *.test.ts, *.spec.ts etc.)
  - Migration count (.sql in supabase/migrations/)
  - Route count (page.tsx for Next.js; .py in nervous_system/ for orchestrator)
  - Top-level directory breakdown (name → file count)
  - Schema tables (CREATE TABLE ... names from supabase/schema.sql)
  - Schema RLS policy count
  - Dependency summary (package.json or requirements.txt first 50 entries)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


def _run(cmd: str, cwd: str) -> str:
    """Run a shell command, return stdout, empty string on error."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _count_files(repo_dir: str) -> dict:
    """Count git-tracked files per top-level directory."""
    files_raw = _run("git ls-files", repo_dir)
    counts: dict[str, int] = {}
    for line in files_raw.splitlines():
        if not line:
            continue
        top = line.split("/")[0] if "/" in line else "(root)"
        counts[top] = counts.get(top, 0) + 1
    return counts


def _total_loc(repo_dir: str) -> int:
    """Count total lines across all git-tracked text files (best effort)."""
    raw = _run(
        "git ls-files | xargs -r wc -l 2>/dev/null | tail -1",
        repo_dir
    )
    try:
        return int(raw.split()[0])
    except (IndexError, ValueError):
        return 0


def _file_count(repo_dir: str) -> int:
    raw = _run("git ls-files | wc -l", repo_dir)
    try:
        return int(raw.strip())
    except ValueError:
        return 0


def _test_count(repo_dir: str) -> int:
    """Count test files."""
    patterns = [
        "git ls-files | grep -E '(test_|_test\\.|spec\\.|tests?/)' | wc -l",
    ]
    for pat in patterns:
        raw = _run(pat, repo_dir)
        try:
            return int(raw.strip())
        except ValueError:
            pass
    return 0


def _migration_count(repo_dir: str) -> int:
    migrations = Path(repo_dir) / "supabase" / "migrations"
    if migrations.is_dir():
        return len(list(migrations.glob("*.sql")))
    return 0


def _route_count(repo_dir: str, repo_name: str) -> int:
    """Count Next.js pages or orchestrator handlers."""
    p = Path(repo_dir)
    if (p / "src" / "app").is_dir():
        # Next.js App Router
        return len(list((p / "src" / "app").rglob("page.tsx")))
    elif (p / "nervous_system").is_dir():
        # Orchestrator
        return len(list((p / "nervous_system").glob("*.py")))
    return 0


def _schema_info(repo_dir: str) -> tuple[list[str], int]:
    """Extract table names + RLS policy count from schema.sql."""
    schema_path = Path(repo_dir) / "supabase" / "schema.sql"
    if not schema_path.exists():
        schema_path = Path(repo_dir) / "schema.sql"
    if not schema_path.exists():
        return [], 0

    content = schema_path.read_text(errors="ignore")
    tables = re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", content, re.IGNORECASE)
    policies = len(re.findall(r"CREATE POLICY", content, re.IGNORECASE))
    return tables, policies


def _dependencies(repo_dir: str) -> dict:
    """Extract dependency summary from package.json or requirements.txt."""
    p = Path(repo_dir)
    pkg = p / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            deps = {
                "dependencies": list((data.get("dependencies") or {}).keys())[:30],
                "devDependencies_count": len(data.get("devDependencies") or {}),
                "manager": "npm",
            }
            return deps
        except Exception:
            pass

    req = p / "requirements.txt"
    if req.exists():
        try:
            lines = [l.strip() for l in req.read_text().splitlines() if l.strip() and not l.startswith("#")]
            return {"packages": lines[:50], "manager": "pip"}
        except Exception:
            pass

    return {}


def _recent_churn(repo_dir: str) -> list[dict]:
    """Top 10 files by commit count in the last 30 days."""
    raw = _run(
        "git log --since='30 days ago' --name-only --format='' | grep -v '^$' | sort | uniq -c | sort -rn | head -20",
        repo_dir
    )
    result = []
    for line in raw.splitlines()[:20]:
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            result.append({"file": parts[1], "commits": int(parts[0])})
    return result


def snapshot(repo_name: str, repo_dir: str, dry_run: bool = False) -> dict:
    """Collect all structural metadata and return the row dict."""
    rdir = str(Path(repo_dir).resolve())

    commit_sha = _run("git rev-parse HEAD", rdir)
    commit_ts = _run("git log -1 --format=%cI HEAD", rdir)
    branch = _run("git rev-parse --abbrev-ref HEAD", rdir) or "main"

    top_dirs = _count_files(rdir)
    tables, rls_count = _schema_info(rdir)

    row = {
        "repo_name": repo_name,
        "commit_sha": commit_sha,
        "commit_timestamp": commit_ts,
        "branch": branch,
        "total_loc": _total_loc(rdir),
        "file_count": _file_count(rdir),
        "test_count": _test_count(rdir),
        "migration_count": _migration_count(rdir),
        "route_count": _route_count(rdir, repo_name),
        "top_level_dirs": top_dirs,
        "recent_churn": _recent_churn(rdir),
        "dependencies": _dependencies(rdir),
        "schema_tables": tables,
        "schema_rls_policies": rls_count,
    }

    if not dry_run:
        from supabase import create_client
        client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
        client.table("repo_snapshot").insert(row).execute()

    return row


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repo name (e.g. ihsanos)")
    parser.add_argument("--dir", required=True, help="Absolute path to repo root")
    parser.add_argument("--dry-run", action="store_true", help="Print row but do not insert")
    args = parser.parse_args(argv)

    try:
        row = snapshot(args.repo, args.dir, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps(row, indent=2, default=str))
        else:
            print(f"write_repo_snapshot: {args.repo} @ {row['commit_sha'][:8]} written", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"write_repo_snapshot failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
