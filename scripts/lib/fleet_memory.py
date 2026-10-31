"""Shared fleet memory — read/write helper over the fleet_memory table.

Memory 2.0 item 2A Part 2 (op#36768, backlog #127). The table itself is
migration 135; 139 added verified_at/expires_at. Everything here goes
through the ONE sanctioned DB write path, fleet_memory_insert(), so the
fail-closed identity contract is enforced in two places rather than one:
agent_identity.resolve_agent_id() refuses to guess in Python, and the
SECURITY DEFINER function refuses an empty created_by at the DB layer.

Reads default to non-retired AND non-expired rows only. Retired rows are
permanent history for audit; they are never the answer to "is this true
now?".
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from scripts.lib.agent_identity import resolve_agent_id

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_SELECT = (
    "SELECT id, scope, scope_key, fact, op_ref, created_by, created_at, "
    "verified_at, expires_at FROM fleet_memory"
)

# Deliberately small: the check is a tripwire against re-asking a fact the
# fleet already recorded, not a semantic matcher. Two shared distinctive
# tokens is enough to catch "DeepSeek console login ... pending" against
# "DeepSeek console login done", while an unrelated ask shares none.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "still",
    "was", "were", "has", "have", "had", "are", "his", "her", "its", "our",
    "you", "your", "not", "but", "any", "all", "can", "now", "done",
})


class FactConflict(RuntimeError):
    """A fact on record says the thing this ask would wait on is settled."""

    def __init__(self, facts):
        self.facts = list(facts)
        detail = "; ".join(f"#{f.id} {f.fact} ({f.op_ref})" for f in self.facts)
        super().__init__(f"fleet_memory already records this as done: {detail}")


@dataclass(frozen=True)
class Fact:
    id: int
    scope: str
    scope_key: str | None
    fact: str
    op_ref: str
    created_by: str
    created_at: datetime
    verified_at: datetime
    expires_at: datetime | None


def _dsn(dsn: str | None = None) -> str:
    if dsn:
        return dsn
    found = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if found:
        return found
    load_dotenv(REPO_ROOT / ".env")
    found = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not found:
        raise RuntimeError("fleet_memory: no DATABASE_URL/SUPABASE_DB_URL resolvable")
    return found


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9_]+", (text or "").lower())
        if len(t) >= 3 and t not in _STOPWORDS
    }


def write_fact(
    scope: str,
    fact: str,
    op_ref: str,
    *,
    scope_key: str | None = None,
    expires_at: datetime | None = None,
    supersedes_id: int | None = None,
    created_by: str | None = None,
    dsn: str | None = None,
    env: dict | None = None,
) -> int:
    """Insert one fact via fleet_memory_insert(). Returns the new row id.

    Identity is resolved fail-closed: no resolvable agent means no write.
    """
    who = created_by or resolve_agent_id(env if env is not None else os.environ)
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fleet_memory_insert(%s, %s, %s, %s, %s, %s, %s, %s)",
            (scope, scope_key, fact, op_ref, who, supersedes_id, None, expires_at),
        )
        return cur.fetchone()[0]


def read_facts(
    *,
    repos=(),
    clients=(),
    include_expired: bool = False,
    dsn: str | None = None,
) -> list[Fact]:
    """Facts visible to a session working on `repos` / `clients`, plus
    fleet-wide ones. Retired rows are always excluded."""
    sql = (
        f"{_SELECT} WHERE retired_at IS NULL "
        "AND (%s OR expires_at IS NULL OR expires_at > now()) "
        "AND (scope = 'fleet' "
        "     OR (scope = 'repo'   AND scope_key = ANY(%s)) "
        "     OR (scope = 'client' AND scope_key = ANY(%s))) "
        "ORDER BY id"
    )
    with psycopg.connect(_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute(sql, (include_expired, list(repos), list(clients)))
        return [Fact(*row) for row in cur.fetchall()]


def facts_matching(
    query: str,
    *,
    repos=(),
    clients=(),
    min_shared: int = 2,
    dsn: str | None = None,
) -> list[Fact]:
    """Facts sharing at least `min_shared` distinctive tokens with `query`."""
    wanted = _tokens(query)
    if not wanted:
        return []
    return [
        f for f in read_facts(repos=repos, clients=clients, dsn=dsn)
        if len(wanted & _tokens(f.fact)) >= min_shared
    ]


def assert_not_pending(ask_text: str, *, repos=(), clients=(), dsn=None) -> None:
    """Refuse to assert something is still pending when a fact says it is done.

    Raises FactConflict naming the fact(s); returns None when clear.
    """
    matched = facts_matching(ask_text, repos=repos, clients=clients, dsn=dsn)
    if matched:
        raise FactConflict(matched)
    return None


def render_facts_block(facts, max_chars: int = 1200) -> str:
    """Compact boot-time rendering. Capped hard: boot context is not a dump."""
    if not facts:
        return ""
    header = "📌 Fleet memory (facts on record):"
    lines = [header]
    for f in facts:
        line = f"  - {f.fact} [{f.op_ref}]"
        if sum(len(x) + 1 for x in lines) + len(line) > max_chars:
            lines.append("  … (more facts on record; not shown)")
            break
        lines.append(line)
    return "\n".join(lines)[:max_chars]
