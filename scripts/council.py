#!/usr/bin/env python3
"""
council.py — Claude Code's side of the Wingmen CTO Council.

The CTO Council is a three-way strategic discussion backed by the
`cto_council` table in Supabase. This script is how Claude Code (the
execution-focused AI voice running in a terminal) participates:

  - `council.py start --question "..." --repo <name>`
      Creates a new session with an opening prompt. Auto-assembles the
      repo context snapshot from the current repo and attaches it to the
      opening message so the strategist has ground truth from turn 1.

  - `council.py post --message "..." [--session <id>] [--repo <path>]`
      Posts a claude_code message to an existing session (defaults to
      the most recently-opened session). Auto-assembles repo context.
      The Postgres trigger fires the cto-strategist Edge Function
      automatically after insert.

  - `council.py read [--session <id>] [--since <time>]`
      Fetches new claude_ai (and system) rows in the session since the
      last read. Prints them to stdout so Claude Code can see the
      strategist's response.

  - `council.py watch [--session <id>] [--interval 10]`
      Polls `read` every N seconds and prints new rows as they arrive.
      Use this when you're actively in a discussion. Ctrl-C to exit.

  - `council.py sessions`
      Lists open sessions (ended_at IS NULL) with their current round
      count and opening prompt.

  - `council.py show --session <id>`
      Prints the full thread for a session. Useful for context after
      reopening a terminal.

## Why this exists

The CTO Strategist (Edge Function) has NO tool access. It can't read
the repo, run git, or query SQL. Its only ground truth about the code
is the `context` JSONB field that claude_code attaches to every row.

If Claude Code forgets to attach context, the strategist speculates
about unseen code — which is the exact failure mode the anti-staleness
architecture was designed to prevent. This script makes attaching
context automatic so it happens every time.

## Usage from Claude Code

When Musa asks me to enter a council session:

    $ python ~/wingmen/orchestrator/scripts/council.py post \\
        --message "Proposal: add shura_sessions table to track multi-turn discussions" \\
        --session 42

The script:
  1. Detects the current repo via `git rev-parse --show-toplevel`
  2. Assembles the context snapshot (branch, commits, files, STATUS.md)
  3. Inserts a claude_code row with the message + context
  4. Returns — the strategist responds asynchronously via the pg trigger

To see the strategist's response:

    $ python ~/wingmen/orchestrator/scripts/council.py read --session 42

Or to watch in real time:

    $ python ~/wingmen/orchestrator/scripts/council.py watch --session 42
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import acreate_client, AsyncClient

# ── Config ────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
ORCHESTRATOR_ROOT = SCRIPT_DIR.parent
load_dotenv(ORCHESTRATOR_ROOT / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Max bytes to read from STATUS.md / CLAUDE.md to avoid context bloat
MAX_STATUS_BYTES = 4000
MAX_CLAUDE_MD_BYTES = 3000
# Max commits to include in recent_commits
MAX_RECENT_COMMITS = 10
# Max files in files_changed_last_10_commits
MAX_FILES_CHANGED = 30


# ── Context assembly ─────────────────────────────────────────────────


def run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def detect_repo_root(start: Path | None = None) -> Path | None:
    """Find the git repo root containing `start` (defaults to cwd)."""
    start = start or Path.cwd()
    top = run_git(["rev-parse", "--show-toplevel"], start)
    return Path(top) if top else None


def head_file(path: Path, max_bytes: int) -> str | None:
    """Read up to max_bytes from a file, or None if it doesn't exist."""
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text.encode("utf-8")) <= max_bytes:
            return text
        # Truncate by character count approximation
        truncated = text[:max_bytes]
        return truncated + "\n\n[... truncated]"
    except OSError:
        return None


def assemble_context(
    repo_path: Path,
    *,
    relevant_snippets: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Build the context payload for a claude_code council message.

    Shape (v1) matches the spec in migration 20260412000001:
      {
        "repo": "<repo dir name>",
        "branch": "<current branch>",
        "recent_commits": ["<hash>: <subject>", ...],
        "files_changed_last_10_commits": [...],
        "uncommitted_files": [...],
        "STATUS_md": "<truncated contents>",
        "CLAUDE_md_head": "<first N bytes>",
        "relevant_snippets": {...},
        "context_version": "v1",
        "assembled_at": "<ISO-8601>"
      }
    """
    repo_path = repo_path.resolve()

    recent_commits_raw = run_git(
        ["log", f"-{MAX_RECENT_COMMITS}", "--format=%h: %s"], repo_path
    )
    recent_commits = recent_commits_raw.splitlines() if recent_commits_raw else []

    files_changed_raw = run_git(
        ["diff", "--name-only", "HEAD~10..HEAD"], repo_path
    )
    files_changed = [
        line.strip()
        for line in (files_changed_raw.splitlines() if files_changed_raw else [])
        if line.strip()
    ][:MAX_FILES_CHANGED]

    uncommitted_raw = run_git(["status", "--porcelain"], repo_path)
    uncommitted_files = (
        uncommitted_raw.splitlines() if uncommitted_raw else []
    )[:MAX_FILES_CHANGED]

    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)

    status_md = head_file(repo_path / "STATUS.md", MAX_STATUS_BYTES)
    claude_md = head_file(repo_path / "CLAUDE.md", MAX_CLAUDE_MD_BYTES)

    return {
        "repo": repo_path.name,
        "repo_abspath": str(repo_path),
        "branch": branch or "unknown",
        "recent_commits": recent_commits,
        "files_changed_last_10_commits": files_changed,
        "uncommitted_files": uncommitted_files,
        "STATUS_md": status_md,
        "CLAUDE_md_head": claude_md,
        "relevant_snippets": relevant_snippets or {},
        "context_version": "v1",
        "assembled_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Supabase client ──────────────────────────────────────────────────


async def get_supabase() -> AsyncClient:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        sys.exit(
            "❌ SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in "
            f"{ORCHESTRATOR_ROOT}/.env"
        )
    return await acreate_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ── Subcommands ──────────────────────────────────────────────────────


async def cmd_start(args: argparse.Namespace) -> int:
    """Create a new session and post the opening prompt."""
    repo_path = detect_repo_root(Path(args.repo) if args.repo else None)
    if not repo_path:
        print(
            "⚠️  Not in a git repo and --repo not supplied. Opening without repo context.",
            file=sys.stderr,
        )
        repo_name: str | None = None
        context: dict[str, Any] = {}
    else:
        repo_name = repo_path.name
        context = assemble_context(repo_path)

    sb = await get_supabase()

    # Create the session row
    session_insert = await sb.table("cto_council_sessions").insert(
        {
            "opening_prompt": args.question,
            "max_rounds": args.max_rounds,
            "repo": repo_name,
            "tags": args.tag or [],
        }
    ).execute()
    session_id = session_insert.data[0]["id"]
    session_public_id = session_insert.data[0]["public_id"]

    # Post the opening prompt as a musa row (represents the question itself)
    await sb.table("cto_council").insert(
        {
            "session_id": session_id,
            "round": 0,
            "role": "musa",
            "message": args.question,
            "context": {},
            "tags": ["OPENING"],
        }
    ).execute()

    # Then post the first claude_code message with the repo context.
    # This is what triggers the strategist's first response.
    first_msg = (
        args.initial_position
        or f"I'm opening this session to discuss: {args.question}\n\n"
        "I don't have a strong opening position yet — I want to hear the strategist's "
        "framing before I commit. Please evaluate the question against CTO principles, "
        "the brain snapshot, and the repo context I've attached."
    )
    await sb.table("cto_council").insert(
        {
            "session_id": session_id,
            "round": 1,
            "role": "claude_code",
            "message": first_msg,
            "context": context,
            "tags": [],
        }
    ).execute()

    print(f"✅ Session {session_id} started ({session_public_id})")
    print(f"   Opening: {args.question}")
    print(f"   Repo: {repo_name or '(none)'}")
    print(f"   Max rounds: {args.max_rounds}")
    print()
    print(
        f"   The strategist should respond within ~10 seconds. Watch with:\n"
        f"   python council.py watch --session {session_id}"
    )
    return 0


async def cmd_post(args: argparse.Namespace) -> int:
    """Post a claude_code message to an existing session with auto-context."""
    sb = await get_supabase()

    # Resolve session_id (either explicit, or default to most recent open)
    session_id = args.session
    if session_id is None:
        result = await sb.table("cto_council_sessions").select(
            "id, opening_prompt, current_round, max_rounds"
        ).is_("ended_at", "null").order("started_at", desc=True).limit(1).execute()
        if not result.data:
            sys.exit("❌ No open session found. Use `council.py start` to create one.")
        session_id = result.data[0]["id"]
        print(f"ℹ️  Using most recent open session: {session_id}")

    # Fetch current session state to know the round number
    session_resp = await sb.table("cto_council_sessions").select(
        "current_round, max_rounds, ended_at"
    ).eq("id", session_id).single().execute()
    session = session_resp.data
    if session.get("ended_at"):
        sys.exit(f"❌ Session {session_id} has already ended. Start a new one.")

    # Assemble context from the current repo
    repo_path = detect_repo_root(Path(args.repo) if args.repo else None)
    if not repo_path:
        print(
            "⚠️  Not in a git repo and --repo not supplied. Posting with empty context.",
            file=sys.stderr,
        )
        context: dict[str, Any] = {}
    else:
        context = assemble_context(repo_path)

    next_round = session["current_round"] + 1

    # Insert the claude_code row. The pg trigger will fire the Edge Function.
    await sb.table("cto_council").insert(
        {
            "session_id": session_id,
            "round": next_round,
            "role": "claude_code",
            "message": args.message,
            "context": context,
            "tags": args.tag or [],
        }
    ).execute()

    print(f"✅ Posted round {next_round} to session {session_id}")
    print(
        f"   Strategist should respond within ~10 seconds. Watch with:\n"
        f"   python council.py watch --session {session_id}"
    )
    return 0


async def cmd_musa(args: argparse.Namespace) -> int:
    """Post a role='musa' message to an existing session. Does NOT fire the
    strategist trigger (the pg trigger only fires on role='claude_code' rows).
    After Musa posts [RULING], Claude Code should post a follow-up claude_code
    row to wake the strategist for [SYNTHESIS]."""
    sb = await get_supabase()

    session_id = args.session
    if session_id is None:
        result = await sb.table("cto_council_sessions").select(
            "id"
        ).is_("ended_at", "null").order("started_at", desc=True).limit(1).execute()
        if not result.data:
            sys.exit("❌ No open session found. Use `council.py start` to create one.")
        session_id = result.data[0]["id"]
        print(f"ℹ️  Using most recent open session: {session_id}")

    session_resp = await sb.table("cto_council_sessions").select(
        "current_round, ended_at"
    ).eq("id", session_id).single().execute()
    session = session_resp.data
    if session.get("ended_at"):
        sys.exit(f"❌ Session {session_id} has already ended.")

    next_round = session["current_round"] + 1
    tags = args.tag or []

    # If Musa tagged [RULING] or [HALT], close the session immediately.
    ended_reason = None
    if "RULING" in tags:
        ended_reason = "musa_ruling"
    elif "HALT" in tags:
        ended_reason = "musa_halt"

    await sb.table("cto_council").insert(
        {
            "session_id": session_id,
            "round": next_round,
            "role": "musa",
            "message": args.message,
            "tags": tags,
            "context": {},
        }
    ).execute()

    if ended_reason:
        await sb.table("cto_council_sessions").update(
            {"ended_at": datetime.now(timezone.utc).isoformat(), "ended_reason": ended_reason}
        ).eq("id", session_id).execute()
        print(f"✅ Posted round {next_round} to session {session_id} — session ended ({ended_reason})")
        if ended_reason == "musa_ruling":
            print("   Next step: run `council.py post --message '...'` to wake the strategist for [SYNTHESIS]")
    else:
        print(f"✅ Posted round {next_round} to session {session_id}")
        print("   Note: musa rows do NOT fire the strategist trigger.")
        print("   To get a strategist response, follow up with `council.py post`.")
    return 0


async def cmd_read(args: argparse.Namespace) -> int:
    """Read new rows from a session since a given time (or start)."""
    sb = await get_supabase()
    session_id = await _resolve_session_id(sb, args.session)

    query = (
        sb.table("cto_council")
        .select("id, round, role, message, tags, created_at")
        .eq("session_id", session_id)
        .order("created_at", desc=False)
    )
    if args.since:
        query = query.gte("created_at", args.since)

    result = await query.execute()
    rows = result.data or []
    if not rows:
        print(f"(no messages in session {session_id} since {args.since or 'start'})")
        return 0

    for row in rows:
        _print_row(row)

    return 0


async def cmd_watch(args: argparse.Namespace) -> int:
    """Poll for new rows in a session, printing as they arrive. Ctrl-C to exit."""
    sb = await get_supabase()
    session_id = await _resolve_session_id(sb, args.session)

    print(f"👁  Watching session {session_id} (poll every {args.interval}s, Ctrl-C to exit)")
    print()

    # Track which message IDs we've already printed
    seen: set[int] = set()

    # Print existing rows first
    existing = await sb.table("cto_council").select(
        "id, round, role, message, tags, created_at"
    ).eq("session_id", session_id).order("created_at", desc=False).execute()
    for row in existing.data or []:
        _print_row(row)
        seen.add(row["id"])

    try:
        while True:
            await asyncio.sleep(args.interval)

            # Fetch the full set and print any new ones
            latest = await sb.table("cto_council").select(
                "id, round, role, message, tags, created_at"
            ).eq("session_id", session_id).order("created_at", desc=False).execute()

            for row in latest.data or []:
                if row["id"] not in seen:
                    _print_row(row)
                    seen.add(row["id"])

            # Check if session has ended
            session_resp = await sb.table("cto_council_sessions").select(
                "ended_at, ended_reason"
            ).eq("id", session_id).single().execute()
            session = session_resp.data
            if session.get("ended_at"):
                print()
                print(f"━━━ Session {session_id} ended ({session['ended_reason']}) ━━━")
                break

    except KeyboardInterrupt:
        print("\n(stopped watching)")

    return 0


async def cmd_sessions(args: argparse.Namespace) -> int:
    """List open sessions."""
    sb = await get_supabase()
    result = await sb.table("cto_council_open_sessions").select("*").execute()
    rows = result.data or []

    if not rows:
        print("(no open sessions)")
        return 0

    print(f"{'ID':<6}{'Rounds':<12}{'Repo':<20}{'Started':<25}{'Opening':<50}")
    print("─" * 113)
    for r in rows:
        rounds = f"{r['current_round']}/{r['max_rounds']}"
        repo = (r.get("repo") or "(none)")[:18]
        started = r["started_at"][:19]
        opening = r["opening_prompt"][:48] + (
            "..." if len(r["opening_prompt"]) > 48 else ""
        )
        print(f"{r['id']:<6}{rounds:<12}{repo:<20}{started:<25}{opening:<50}")

    return 0


async def cmd_show(args: argparse.Namespace) -> int:
    """Print the full thread for a session."""
    sb = await get_supabase()
    session_id = args.session

    # Fetch session meta
    session_resp = await sb.table("cto_council_sessions").select("*").eq(
        "id", session_id
    ).single().execute()
    session = session_resp.data

    print(f"━━━ Session {session_id} ({session['public_id']}) ━━━")
    print(f"Opening: {session['opening_prompt']}")
    print(f"Repo: {session.get('repo') or '(none)'}")
    print(f"Rounds: {session['current_round']}/{session['max_rounds']}")
    print(f"Status: {'ENDED (' + session['ended_reason'] + ')' if session['ended_at'] else 'OPEN'}")
    print(f"Tokens: {session['token_count']}  USD: ${session['usd_estimated']}")
    print(f"Had pushback: {session['had_pushback']}")
    print()

    # Fetch all rows
    thread = await sb.table("cto_council").select(
        "id, round, role, message, tags, created_at"
    ).eq("session_id", session_id).order("created_at", desc=False).execute()

    for row in thread.data or []:
        _print_row(row)

    return 0


# ── Helpers ──────────────────────────────────────────────────────────


async def _resolve_session_id(sb: AsyncClient, explicit: int | None) -> int:
    """Resolve the session ID to use — either the explicit arg or the most recent open."""
    if explicit is not None:
        return explicit
    result = await sb.table("cto_council_sessions").select("id").is_(
        "ended_at", "null"
    ).order("started_at", desc=True).limit(1).execute()
    if not result.data:
        sys.exit("❌ No open session found and --session not supplied.")
    return result.data[0]["id"]


def _print_row(row: dict[str, Any]) -> None:
    """Render a single thread row for terminal display."""
    role = row["role"]
    icon = {
        "musa": "👤",
        "claude_code": "🛠️ ",
        "claude_ai": "🧠",
        "system": "⚙️ ",
    }.get(role, "?")
    ts = row["created_at"][:19].replace("T", " ")
    tags = " ".join(f"[{t}]" for t in (row.get("tags") or []))
    label = {
        "musa": "Musa",
        "claude_code": "Claude Code",
        "claude_ai": "CTO Strategist",
        "system": "System",
    }.get(role, role)
    header = f"━━━ {icon} {label}  ·  round {row['round']}  ·  {ts}  {tags}".rstrip()
    print(header)
    print(row["message"])
    print()


# ── CLI ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Wingmen CTO Council — Claude Code's side",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # start
    p_start = sub.add_parser("start", help="Create a new council session")
    p_start.add_argument("--question", required=True, help="The opening question / prompt")
    p_start.add_argument("--repo", help="Path to repo (default: detect from cwd)")
    p_start.add_argument(
        "--initial-position",
        help="Claude Code's opening position (optional; defaults to a neutral 'let's discuss')",
    )
    p_start.add_argument("--max-rounds", type=int, default=6)
    p_start.add_argument("--tag", action="append", help="Session tag (repeatable)")

    # post
    p_post = sub.add_parser("post", help="Post a claude_code message to a session")
    p_post.add_argument("--message", required=True, help="The message body")
    p_post.add_argument("--session", type=int, help="Session ID (default: most recent open)")
    p_post.add_argument("--repo", help="Path to repo (default: detect from cwd)")
    p_post.add_argument("--tag", action="append", help="Message tag (repeatable)")

    # musa
    p_musa = sub.add_parser("musa", help="Post a role='musa' message to a session (for the human ruler)")
    p_musa.add_argument("--message", required=True, help="The message body")
    p_musa.add_argument("--session", type=int, help="Session ID (default: most recent open)")
    p_musa.add_argument(
        "--tag", action="append",
        help="Message tag (repeatable). Use RULING to end session with your decision, HALT to stop without one.",
    )

    # read
    p_read = sub.add_parser("read", help="Read rows from a session")
    p_read.add_argument("--session", type=int, help="Session ID (default: most recent open)")
    p_read.add_argument("--since", help="ISO timestamp to filter from (optional)")

    # watch
    p_watch = sub.add_parser("watch", help="Watch a session for new rows (polls every N seconds)")
    p_watch.add_argument("--session", type=int, help="Session ID (default: most recent open)")
    p_watch.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")

    # sessions
    sub.add_parser("sessions", help="List open sessions")

    # show
    p_show = sub.add_parser("show", help="Print the full thread for a session")
    p_show.add_argument("--session", type=int, required=True, help="Session ID")

    return parser


async def main_async() -> int:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "start": cmd_start,
        "post": cmd_post,
        "musa": cmd_musa,
        "read": cmd_read,
        "watch": cmd_watch,
        "sessions": cmd_sessions,
        "show": cmd_show,
    }
    handler = handlers.get(args.cmd)
    if not handler:
        parser.print_help()
        return 1
    return await handler(args)


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
