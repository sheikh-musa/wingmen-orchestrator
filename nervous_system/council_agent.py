"""Autonomous Council Agent — plays the Claude Code role without the CLI.

Watches for Al-Mushtashir responses in open council sessions and
generates claude_code replies using the Claude API with read-only
tools. Fresh repo context is assembled every round via council.py's
assemble_context(). Musa watches in Telegram via the live relay and
can /rule at any point.

Architecture (from CTO Council session 6, 2026-04-13):
- Discussion: Claude API (Sonnet) + read-only tools
- Execution: session 4 executor (separate system)
- Context: fresh assemble_context() per round, not stale pre-load
- Safety: read-only tools only — no bash, no file writes, no deploys

Origin: Musa's requirement that councils run without the CLI open.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

logger = logging.getLogger("wingmen.council_agent")

ORCH_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str) -> str:
    raw = os.environ.get(name, "")
    return raw.split("#", 1)[0].strip()


# ── Tool definitions for the Claude API ───────────────────────────

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the repository. Returns the file text. Use for checking source code, configs, migrations, STATUS.md, CLAUDE.md.",
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from repo root (e.g., 'src/app/dashboard/layout.tsx')",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Max lines to return (default 200). Use for large files.",
                    "default": 200,
                },
            },
        },
    },
    {
        "name": "grep",
        "description": "Search for a pattern in the repository. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default: repo root)",
                    "default": ".",
                },
                "max_results": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "list_files",
        "description": "List files matching a glob pattern in the repository.",
        "input_schema": {
            "type": "object",
            "required": ["glob_pattern"],
            "properties": {
                "glob_pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g., 'src/app/**/*.tsx', 'supabase/migrations/*.sql')",
                },
            },
        },
    },
    {
        "name": "git_log",
        "description": "Get recent git commit history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of commits (default 10)", "default": 10},
                "path": {"type": "string", "description": "Filter to commits touching this path"},
            },
        },
    },
    {
        "name": "execute_sql",
        "description": "Run a SELECT query against Supabase. READ ONLY — no INSERT/UPDATE/DELETE.",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "SQL SELECT query. Must start with SELECT.",
                },
            },
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────


def _execute_tool(name: str, input_data: dict, repo_path: Path) -> str:
    """Execute a read-only tool and return the result as a string."""
    try:
        if name == "read_file":
            path = repo_path / input_data["path"]
            if not path.exists():
                return f"File not found: {input_data['path']}"
            max_lines = input_data.get("max_lines", 200)
            lines = path.read_text(errors="replace").splitlines()
            if len(lines) > max_lines:
                return "\n".join(lines[:max_lines]) + f"\n\n... ({len(lines) - max_lines} more lines)"
            return "\n".join(lines)

        elif name == "grep":
            pattern = input_data["pattern"]
            search_path = input_data.get("path", ".")
            max_results = input_data.get("max_results", 20)
            proc = subprocess.run(
                ["grep", "-rn", "--include=*.ts", "--include=*.tsx", "--include=*.py",
                 "--include=*.sql", "--include=*.md", "--include=*.json",
                 "-m", str(max_results), pattern, search_path],
                capture_output=True, text=True, cwd=str(repo_path), timeout=10,
            )
            return proc.stdout[:5000] or "(no matches)"

        elif name == "list_files":
            import glob as glob_mod
            pattern = input_data["glob_pattern"]
            matches = sorted(glob_mod.glob(str(repo_path / pattern), recursive=True))
            relative = [str(Path(m).relative_to(repo_path)) for m in matches[:50]]
            return "\n".join(relative) or "(no matches)"

        elif name == "git_log":
            n = input_data.get("n", 10)
            cmd = ["git", "log", f"-{n}", "--oneline"]
            if input_data.get("path"):
                cmd.extend(["--", input_data["path"]])
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(repo_path), timeout=10,
            )
            return proc.stdout[:3000] or "(no commits)"

        elif name == "execute_sql":
            query = input_data["query"].strip()
            if not query.upper().startswith("SELECT"):
                return "ERROR: Only SELECT queries allowed. This is a read-only tool."
            from supabase import create_client
            sb = create_client(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY"))
            result = sb.rpc("exec_sql", {"query": query}).execute()
            return json.dumps(result.data, default=str)[:3000]

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error: {e}"


# ── Context assembly ──────────────────────────────────────────────


def _build_system_prompt(repo_path: Path) -> str:
    """Build the system prompt for the Claude Code council agent."""
    # Load CLAUDE.md
    claude_md = ""
    claude_path = repo_path / "CLAUDE.md"
    if claude_path.exists():
        claude_md = claude_path.read_text(errors="replace")[:6000]

    # Load MEMORY.md
    memory_md = ""
    memory_path = Path.home() / ".claude" / "projects" / "-Users-sheikhmusa" / "memory" / "MEMORY.md"
    if memory_path.exists():
        memory_md = memory_path.read_text(errors="replace")[:3000]

    return f"""You are Claude Code — the implementation-focused AI voice in the Wingmen CTO Council.

Your role: propose concrete solutions, write specs, defend your proposals against Al-Mushtashir's pushback, and converge on decisions that Musa (the human founder) can approve.

You have read-only tools to check the repo, search code, read files, and query the database. Use them to ground your proposals in real code — never speculate about what a file contains when you can read it.

## Response Format
Structure every response exactly like this:
## Position
[Your view in one paragraph]
## Concerns
[Numbered list citing specific facts from tools/context]
## Tag
[One tag: [PUSHBACK], [CONCUR], [INSUFFICIENT_CONTEXT], or [SPEC_APPROVED]]

## Repository Context (CLAUDE.md)
{claude_md}

## Memory
{memory_md}"""


def _build_thread_messages(thread: list[dict]) -> list[dict]:
    """Convert cto_council rows into Claude API messages."""
    messages: list[dict] = []
    role_labels = {
        "musa": "MUSA",
        "claude_code": "CLAUDE CODE",
        "claude_ai": "AL-MUSHTASHIR",
        "system": "SYSTEM",
    }
    for row in thread:
        role = row.get("role", "system")
        label = role_labels.get(role, role.upper())
        tags = " ".join(f"[{t}]" for t in (row.get("tags") or []))
        text = f"[{label} — round {row.get('round', 0)}] {tags}\n{row.get('message', '')}"

        if role in ("musa", "claude_ai", "system"):
            messages.append({"role": "user", "content": text})
        elif role == "claude_code":
            messages.append({"role": "assistant", "content": text})

    return messages


# ── Main agent loop ───────────────────────────────────────────────


async def run_council_agent(sb) -> None:
    """Check for open sessions needing a claude_code response and post one.

    Called from wingmen_orch.py main loop. Only acts if:
    - Session is open (ended_at IS NULL)
    - Latest message is from claude_ai (Al-Mushtashir responded, Claude Code hasn't yet)
    - No human has posted since (Musa hasn't /rule'd)
    """
    api_key = _env("ANTHROPIC_API_KEY")
    if not api_key:
        return

    try:
        # Find open sessions
        sessions = await (
            sb.table("cto_council_sessions")
            .select("id, repo, current_round, max_rounds")
            .is_("ended_at", "null")
            .execute()
        )

        for session in (sessions.data or []):
            session_id = session["id"]
            repo_name = session.get("repo") or "orchestrator"

            # Get the latest message
            latest = await (
                sb.table("cto_council")
                .select("role, round, tags")
                .eq("session_id", session_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

            if not latest.data:
                continue

            last_role = latest.data[0].get("role")
            last_tags = latest.data[0].get("tags") or []

            # Only respond if the last message is from Al-Mushtashir
            # (meaning it's Claude Code's turn) and the session isn't done
            if last_role != "claude_ai":
                continue

            # Don't respond to SYNTHESIS or SPEC_APPROVED (session is winding down)
            if "SYNTHESIS" in last_tags:
                continue

            # Check round limit
            current_round = session.get("current_round", 0)
            if current_round >= session.get("max_rounds", 6) - 1:
                continue

            # It's our turn — generate a response
            await _generate_response(sb, session_id, repo_name, api_key)

    except Exception as e:
        logger.error(f"council_agent failed: {e}")


async def _generate_response(
    sb, session_id: int, repo_name: str, api_key: str
) -> None:
    """Generate and post a claude_code response to a council session."""
    # Resolve repo path
    import context_loader
    try:
        config = context_loader.get_repo_config(repo_name)
        repo_path = Path(os.path.expanduser(config["local_path"]))
    except (ValueError, KeyError):
        repo_path = ORCH_ROOT

    # Load the full thread
    thread_result = await (
        sb.table("cto_council")
        .select("round, role, message, tags, context")
        .eq("session_id", session_id)
        .order("created_at", desc=False)
        .execute()
    )
    thread = thread_result.data or []

    # Assemble fresh context
    from scripts.council import assemble_context
    try:
        context = assemble_context(repo_path)
    except Exception:
        context = {}

    # Build API messages
    system_prompt = _build_system_prompt(repo_path)
    messages = _build_thread_messages(thread)

    # Add fresh context as a user message
    messages.append({
        "role": "user",
        "content": f"[SYSTEM — fresh repo context]\n```json\n{json.dumps(context, default=str)[:4000]}\n```\nIt is now your turn to respond. Use tools if you need to check specific files.",
    })

    # Call Claude API with tools.
    # llm_route_exempt: tool_use_with_caller_defined_tools
    # CAI-PROCESS-MAX-FIRST-001 Carve-Out 5. council_agent uses Anthropic
    # tool-use with 5 caller-defined tools (read_file, grep, list_files,
    # git_log, sql) executed locally via the dispatcher in this module.
    # CLI `claude -p` exposes Claude's own tools (Bash, Read, Edit) but
    # cannot inject caller-defined-with-caller-execution tool surfaces.
    client = anthropic.Anthropic(api_key=api_key)

    try:
        # Tool-use loop — Claude may call tools multiple times
        max_tool_rounds = 5
        for _ in range(max_tool_rounds):
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )

            # Check if response has tool calls
            tool_calls = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if not tool_calls:
                # No more tool calls — we have the final response
                final_text = "\n".join(b.text for b in text_blocks)
                break

            # Execute tools and feed results back
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tc in tool_calls:
                result = _execute_tool(tc.name, tc.input, repo_path)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })
            messages.append({"role": "user", "content": tool_results})
        else:
            final_text = "Tool loop exceeded maximum rounds. Partial response may be incomplete."

    except Exception as e:
        logger.error(f"council_agent API call failed for session {session_id}: {e}")
        return

    if not final_text or len(final_text.strip()) < 20:
        logger.warning(f"council_agent: empty response for session {session_id}")
        return

    # Post to cto_council as claude_code
    current_round = thread[-1].get("round", 0) + 1 if thread else 1
    await sb.table("cto_council").insert({
        "session_id": session_id,
        "round": current_round,
        "role": "claude_code",
        "message": final_text,
        "tags": [],
        "context": {
            **context,
            "source": "council_agent",
            "model": "claude-sonnet-4-20250514",
        },
    }).execute()

    logger.info(
        f"council_agent: posted round {current_round} to session {session_id} "
        f"(usage: {response.usage.input_tokens}in/{response.usage.output_tokens}out)"
    )
