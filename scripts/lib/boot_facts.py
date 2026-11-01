"""Boot-time fleet facts — the small, fail-safe read every session start can
afford (Memory 2.0 Part 2, op#36768 / backlog #127).

The operator's complaint that started this: the fleet forgot a fact Musa had
already given and asked him for it again. The cure is a durable fact table
(migration 135 + 139, scripts/lib/fleet_memory.py) AND a boot that reads it.

The budget is the design, not a detail (console ruling, bus #87068):
  * the whole hook stays under 3s,
  * any error or a cold DB prints NOTHING and exits 0,
  * output stays a few hundred tokens, scoped to this session, never a dump.

A boot that waits on memory is worse than a boot without memory: it delays
every session on the box, including ones with no fleet work in them.
"""
from __future__ import annotations

import json
import os

_FACTS_TIMEOUT_S = 1.5  # of the 3s hook budget; libpq connect: 1s + query: 1.5s
_FACTS_MAX_CHARS = 900  # ~250 tokens


def facts_block(project_dir: str | None = None) -> str:
    """Facts on record for this session's scope, or "" for any reason at all.

    Scope comes from where the session is (repo = directory name) and what it
    declares (CC_CLIENT, comma-separated) -- never from a global dump.
    """
    try:
        from scripts.lib import fleet_memory

        cwd = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        repo = os.path.basename(os.path.abspath(cwd)) if cwd else ""
        clients = [c.strip() for c in os.environ.get("CC_CLIENT", "").split(",") if c.strip()]
        facts = fleet_memory.read_facts(
            repos=[repo] if repo else [],
            clients=clients,
            timeout_s=_FACTS_TIMEOUT_S,
        )
        return fleet_memory.render_facts_block(facts, max_chars=_FACTS_MAX_CHARS)
    except Exception:
        return ""


def emit(block: str) -> None:
    """Print the SessionStart hook payload. Silence when there is nothing."""
    if not block:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": block,
        }
    }))
