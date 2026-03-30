"""Loads per-repo context for the orchestrator."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from supabase import AsyncClient as SupabaseAsyncClient


REPOS_JSON = Path(__file__).parent / "REPOS.json"


def _load_repos() -> list[dict]:
    with open(REPOS_JSON) as f:
        return json.load(f)["repos"]


def get_repo_config(repo_name: str) -> dict:
    for repo in _load_repos():
        if repo["name"] == repo_name:
            return repo
    raise ValueError(f"Repo '{repo_name}' not found in REPOS.json")


def _resolve_path(raw_path: str) -> Path:
    return Path(os.path.expanduser(raw_path))


def _read_file_safe(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


async def load_context(repo_name: str, supabase: SupabaseAsyncClient) -> dict:
    """Load full context for a repo: CLAUDE.md, STATUS.md, memory, config."""
    config = get_repo_config(repo_name)
    repo_path = _resolve_path(config["local_path"])

    claude_md = _read_file_safe(repo_path / "CLAUDE.md")
    status_md = _read_file_safe(repo_path / "STATUS.md")

    # Query repo_memory table
    result = await supabase.table("repo_memory").select("key, value").eq(
        "repo_name", repo_name
    ).execute()
    memory = result.data if result.data else []

    return {
        "claude_md": claude_md,
        "status_md": status_md,
        "memory": memory,
        "repo_config": config,
        "repo_path": str(repo_path),
    }


async def load_brainstorm_context(repo_name: str, supabase: SupabaseAsyncClient) -> dict:
    """Load context for brainstorm mode — includes git log."""
    ctx = await load_context(repo_name, supabase)

    # Get recent git history
    repo_path = ctx["repo_path"]
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "-5",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            ctx["recent_commits"] = stdout.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            proc.kill()
            ctx["recent_commits"] = ""
    except Exception:
        ctx["recent_commits"] = ""

    # Get directory structure (fast — dirs only, depth 2)
    try:
        proc = await asyncio.create_subprocess_exec(
            "find", ".", "-maxdepth", "2", "-type", "d",
            "-not", "-path", "./.git*",
            "-not", "-path", "./node_modules*",
            "-not", "-path", "./.next*",
            "-not", "-path", "./__pycache__*",
            "-not", "-path", "./.venv*",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            ctx["file_tree"] = stdout.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            proc.kill()
            ctx["file_tree"] = ""
    except Exception:
        ctx["file_tree"] = ""

    return ctx


async def get_client_repos(client_id: int, supabase: SupabaseAsyncClient) -> list[dict]:
    """Get all repo configs for a client."""
    result = await supabase.table("client_repos").select("repo_name").eq(
        "client_id", client_id
    ).execute()
    repo_names = [r["repo_name"] for r in (result.data or [])]
    repos = []
    for name in repo_names:
        try:
            repos.append(get_repo_config(name))
        except ValueError:
            pass
    return repos


def get_all_repo_names() -> list[str]:
    """Return all repo names from REPOS.json."""
    return [r["name"] for r in _load_repos()]
